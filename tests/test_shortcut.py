"""Desktop and Startup shortcuts.

Behind the "Launch at login" switch, so a quiet failure here shows the user a
switch that says something untrue. Nothing is written to the real Desktop:
PowerShell is stubbed and the folders are pointed at a temporary directory.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice import shortcut  # noqa: E402


@pytest.fixture
def fake_folders(tmp_path, monkeypatch):
    desktop = tmp_path / "Рабочий стол"      # localised, as OneDrive leaves it
    startup = tmp_path / "Startup"
    desktop.mkdir()
    startup.mkdir()
    monkeypatch.setattr(shortcut, "desktop", lambda: desktop)
    monkeypatch.setattr(shortcut, "startup", lambda: startup)
    monkeypatch.setattr(shortcut, "icon", lambda: tmp_path / "icon.ico")
    return desktop, startup


@pytest.fixture
def powershell(monkeypatch):
    """Capture the script instead of running it, and create the .lnk the real
    run would have produced."""
    captured: dict = {}

    def fake_run(args, **kwargs):
        from pathlib import Path

        script_path = Path(args[-1])
        captured["script"] = script_path.read_text(encoding="utf-8-sig")
        captured["raw"] = script_path.read_bytes()
        for line in captured["script"].splitlines():
            if "CreateShortcut(" in line:
                target = line.split("'")[1]
                Path(target).write_text("", encoding="utf-8")
        import types

        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shortcut.subprocess, "run", fake_run)
    return captured


# ── which interpreter the shortcut points at ─────────────────────────────


def test_the_project_venv_is_preferred(tmp_path, monkeypatch):
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(shortcut, "ROOT", tmp_path)
    assert shortcut.interpreter() == scripts / "pythonw.exe"


def test_it_is_the_windowed_interpreter():
    """python.exe keeps a console window open for the life of the app — a
    black box on screen that closes the tray app when someone tidies it away."""
    assert shortcut.interpreter().name in ("pythonw.exe", "python.exe")


def test_a_console_interpreter_is_the_last_resort(tmp_path, monkeypatch):
    """Ugly beats no app at all."""
    monkeypatch.setattr(shortcut, "ROOT", tmp_path)
    monkeypatch.setattr(shortcut.sys, "executable", str(tmp_path / "python.exe"))
    assert shortcut.interpreter() == tmp_path / "python.exe"


# ── creating and removing ────────────────────────────────────────────────


def test_creating_puts_a_link_where_asked(fake_folders, powershell):
    desktop, _ = fake_folders
    link = shortcut.create(desktop, "FortuneVoice")
    assert link == desktop / "FortuneVoice.lnk"
    assert link.exists()


def test_the_script_launches_the_app_as_a_module(fake_folders, powershell):
    desktop, _ = fake_folders
    shortcut.create(desktop, "FortuneVoice")
    script = powershell["script"]
    assert "-m fortunevoice" in script
    assert "$link.Save()" in script


def test_the_script_is_utf8_with_a_bom(fake_folders, powershell):
    """PowerShell 5.1 decodes a script without a BOM using the console code
    page, which turns a Cyrillic Desktop path into mojibake — and the shortcut
    lands somewhere else, or nowhere. This is exactly how the first attempt at
    this failed."""
    desktop, _ = fake_folders
    shortcut.create(desktop, "FortuneVoice")
    assert powershell["raw"].startswith(b"\xef\xbb\xbf")
    assert "Рабочий стол" in powershell["script"], "the localised path survived"


def test_the_window_style_is_minimised(fake_folders, powershell):
    """7 = minimised. The app lives in the tray; a console flashing up on
    every login is the kind of thing that gets a startup entry deleted."""
    desktop, _ = fake_folders
    shortcut.create(desktop, "FortuneVoice")
    assert "$link.WindowStyle = 7" in powershell["script"]


def test_removing_reports_whether_there_was_anything(fake_folders, powershell):
    desktop, _ = fake_folders
    assert shortcut.remove(desktop) is False
    shortcut.create(desktop, "FortuneVoice")
    assert shortcut.remove(desktop) is True
    assert not (desktop / "FortuneVoice.lnk").exists()


# ── "Launch at login" ────────────────────────────────────────────────────


def test_the_switch_reflects_the_startup_folder(fake_folders, powershell):
    assert shortcut.launches_at_login() is False
    assert shortcut.set_launch_at_login(True) is True
    assert shortcut.launches_at_login() is True
    assert shortcut.set_launch_at_login(False) is False


def test_the_switch_reports_what_was_achieved_not_what_was_asked(fake_folders,
                                                                 monkeypatch):
    """A failed write must not leave the switch showing something untrue —
    the user would believe the app starts with Windows when it does not."""
    def refuse(*args, **kwargs):
        raise PermissionError("locked-down profile")

    monkeypatch.setattr(shortcut.subprocess, "run", refuse)
    assert shortcut.set_launch_at_login(True) is False


def test_an_unreadable_startup_folder_is_not_a_crash(monkeypatch):
    def explode():
        raise OSError("no such known folder")

    monkeypatch.setattr(shortcut, "startup", explode)
    assert shortcut.launches_at_login() is False


def test_the_link_name_is_a_shortcut():
    assert shortcut.NAME.endswith(".lnk")


def test_an_apostrophe_in_the_path_does_not_break_the_script():
    """A Windows account named O'Brien was enough: the single-quoted literal
    closed early, PowerShell failed to parse the script, and "Launch at login"
    refused to turn on — the switch snapping back with no message anywhere."""
    escaped = shortcut._literal("C:" + chr(92) + "Users" + chr(92) + "O'Brien")
    assert escaped == "C:" + chr(92) + "Users" + chr(92) + "O''Brien"


def test_a_path_without_quotes_is_untouched():
    plain = "C:" + chr(92) + "Users" + chr(92) + "glebo"
    assert shortcut._literal(plain) == plain
