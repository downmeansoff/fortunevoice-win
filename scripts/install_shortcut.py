"""Create the Desktop shortcut (and optionally a startup one).

    python scripts/install_shortcut.py
    python scripts/install_shortcut.py --startup     # also launch at login
    python scripts/install_shortcut.py --remove

Two details that decide whether this actually works:

* **pythonw.exe, not python.exe.** `python.exe` keeps a console window open for
  the life of the app — a black box on screen that closes the tray app when
  someone tidies it away.
* **The real Desktop, not `%USERPROFILE%\\Desktop`.** With OneDrive's folder
  backup on, the real one is `%OneDrive%\\Рабочий стол` (localised!), and a
  file written to the literal path is invisible in Explorer. The shell is
  asked where it is rather than guessed.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "FortuneVoice.lnk"

# SHGetKnownFolderPath, not a PowerShell round-trip: this user's Desktop is
# "C:\Users\...\OneDrive\Рабочий стол", and piping that back through a console
# mangles it into the OEM code page. Asking Windows directly returns real
# UTF-16 with no encoding in the middle.
FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_Startup = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]


def shell_folder(folder_id: str) -> Path:
    guid = _GUID()
    ole32 = ctypes.windll.ole32
    if ole32.CLSIDFromString(ctypes.c_wchar_p(folder_id), ctypes.byref(guid)) != 0:
        raise RuntimeError(f"bad folder id {folder_id}")
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        raise RuntimeError(f"could not resolve {folder_id}")
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


def interpreter() -> Path:
    """The windowed interpreter to launch with, preferring the project venv."""
    venv = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv.exists():
        return venv
    beside = Path(sys.executable).with_name("pythonw.exe")
    if beside.exists():
        return beside
    # No pythonw anywhere: a console window is ugly but better than no app.
    return Path(sys.executable)


def icon() -> Path:
    path = ROOT / "assets" / "fortunevoice.ico"
    if not path.exists():
        sys.path.insert(0, str(ROOT / "src"))
        from fortunevoice import assets  # noqa: PLC0415

        assets.write_ico(path)
    return path


def create(target_dir: Path, description: str) -> Path:
    link = target_dir / NAME
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut('{link}')
$link.TargetPath = '{interpreter()}'
$link.Arguments = '-m fortunevoice'
$link.WorkingDirectory = '{ROOT}'
$link.IconLocation = '{icon()},0'
$link.Description = '{description}'
$link.WindowStyle = 7
$link.Save()
"""
    # Written to a file as UTF-8 *with BOM* rather than passed with -Command:
    # PowerShell decodes an inline command with the console code page, which
    # turns a Cyrillic path into mojibake and the shortcut into an error.
    # The BOM is what makes it read the file as UTF-8.
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8-sig") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path],
            check=True, capture_output=True, text=True,
        )
    finally:
        Path(script_path).unlink(missing_ok=True)
    return link


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup", action="store_true",
                        help="also add a shortcut to the Startup folder")
    parser.add_argument("--remove", action="store_true", help="delete both shortcuts")
    args = parser.parse_args()

    desktop = shell_folder(FOLDERID_Desktop)
    startup = shell_folder(FOLDERID_Startup)

    if args.remove:
        for folder in (desktop, startup):
            link = folder / NAME
            if link.exists():
                link.unlink()
                print(f"removed {link}")
        return 0

    print(f"created {create(desktop, 'Local voice dictation - hold a hotkey and speak')}")
    if args.startup:
        print(f"created {create(startup, 'FortuneVoice (starts with Windows)')}")
    print(f"\nlaunching: {interpreter()} -m fortunevoice")
    print(f"from:      {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
