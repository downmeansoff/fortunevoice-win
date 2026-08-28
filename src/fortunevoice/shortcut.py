"""Desktop and Startup shortcuts.

Lives in the package rather than only in `scripts/` because Settings has a
"Launch at login" switch, and that switch is exactly "is there a shortcut in
the Startup folder".

Two details decide whether this works at all:

* **pythonw.exe, not python.exe.** `python.exe` keeps a console window open
  for the life of the app — a black box on screen that closes the tray app
  when someone tidies it away.
* **Folder paths come from `SHGetKnownFolderPath`.** With OneDrive folder
  backup on, the real Desktop is `%OneDrive%\\Рабочий стол` — localised, and
  not what `%USERPROFILE%\\Desktop` points at. Asking Windows returns real
  UTF-16; routing the answer through a PowerShell pipe mangles it into the
  console code page, which is how the first attempt at this failed.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

from .log import get as get_logger

logger = get_logger("shortcut")

ROOT = Path(__file__).resolve().parent.parent.parent
NAME = "FortuneVoice.lnk"

FOLDERID_Desktop = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_Startup = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD), ("Data4", ctypes.c_byte * 8),
    ]


def shell_folder(folder_id: str) -> Path:
    guid = _GUID()
    ole32 = ctypes.windll.ole32
    if ole32.CLSIDFromString(ctypes.c_wchar_p(folder_id), ctypes.byref(guid)) != 0:
        raise RuntimeError(f"bad folder id {folder_id}")
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(out)
    ) != 0:
        raise RuntimeError(f"could not resolve {folder_id}")
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


def desktop() -> Path:
    return shell_folder(FOLDERID_Desktop)


def startup() -> Path:
    return shell_folder(FOLDERID_Startup)


def interpreter() -> Path:
    """The windowed interpreter to launch with, preferring the project venv."""
    venv = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv.exists():
        return venv
    beside = Path(sys.executable).with_name("pythonw.exe")
    if beside.exists():
        return beside
    return Path(sys.executable)  # console window is ugly, but better than no app


def icon() -> Path:
    path = ROOT / "assets" / "fortunevoice.ico"
    if not path.exists():
        from . import assets

        assets.write_ico(path)
    return path


def _literal(value: object) -> str:
    """Escape for a PowerShell single-quoted string, where doubling the
    quote is the only escape there is.

    Without this an apostrophe anywhere in the path — a Windows account
    named O'Brien is all it takes — closed the literal early. PowerShell
    then failed to parse the script, and "Launch at login" refused to turn
    on with the switch snapping back and no message anywhere.
    """
    return str(value).replace("'", "''")


def create(folder: Path, description: str) -> Path:
    link = folder / NAME
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut('{_literal(link)}')
$link.TargetPath = '{_literal(interpreter())}'
$link.Arguments = '-m fortunevoice'
$link.WorkingDirectory = '{_literal(ROOT)}'
$link.IconLocation = '{_literal(icon())},0'
$link.Description = '{_literal(description)}'
$link.WindowStyle = 7
$link.Save()
"""
    # Written to a file as UTF-8 *with BOM* rather than passed with -Command:
    # PowerShell decodes an inline command using the console code page, which
    # turns a Cyrillic path into mojibake. The BOM makes it read UTF-8.
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


def remove(folder: Path) -> bool:
    link = folder / NAME
    if link.exists():
        link.unlink()
        return True
    return False


# ── "Launch at login" ────────────────────────────────────────────────────


def launches_at_login() -> bool:
    try:
        return (startup() / NAME).exists()
    except Exception:  # noqa: BLE001 - a locked-down profile is not a crash
        return False


def set_launch_at_login(enabled: bool) -> bool:
    """Returns the state actually achieved, so a failed write can't leave the
    switch showing something untrue."""
    try:
        if enabled:
            create(startup(), "FortuneVoice (starts with Windows)")
        else:
            remove(startup())
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not change launch-at-login: %s", exc)
    return launches_at_login()
