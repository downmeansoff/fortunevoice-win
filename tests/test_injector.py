"""Typing text into another application.

This is the part of the app that actually delivers, and until now the only
thing testing it was a person watching characters appear. The tests here drive
the real code and inspect the INPUT structures it builds, stubbing only
`_send` — the one call that would reach the rest of the desktop.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice import injector  # noqa: E402

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


@pytest.fixture
def sent(monkeypatch):
    """Every INPUT the code hands to SendInput, flattened in order."""
    captured: list = []

    def fake_send(events):
        captured.extend(events)
        return True

    monkeypatch.setattr(injector, "_send", fake_send)
    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    # No modifier is physically held unless a test says so.
    monkeypatch.setattr(injector.user32, "GetAsyncKeyState", lambda _vk: 0)
    return captured


def units(captured) -> list[int]:
    """The UTF-16 code units of the key-down events, in order."""
    return [e.ki.wScan for e in captured
            if e.ki.dwFlags & KEYEVENTF_UNICODE
            and not e.ki.dwFlags & KEYEVENTF_KEYUP]


def test_ascii_is_typed_as_unicode_code_units(sent):
    assert injector.type_text("Hi") is True
    assert units(sent) == [ord("H"), ord("i")]


def test_cyrillic_survives(sent):
    """The whole point of KEYEVENTF_UNICODE: no keyboard layout is involved,
    so Russian types the same on a US layout."""
    injector.type_text("привет")
    assert units(sent) == [ord(c) for c in "привет"]


def test_emoji_is_split_into_a_surrogate_pair(sent):
    """Above the BMP one character is two UTF-16 units, and sending only the
    first would type half a character."""
    injector.type_text("🙂")
    assert units(sent) == [0xD83D, 0xDE42]


def test_every_key_down_has_a_key_up(sent):
    injector.type_text("abc")
    downs = [e for e in sent if not e.ki.dwFlags & KEYEVENTF_KEYUP]
    ups = [e for e in sent if e.ki.dwFlags & KEYEVENTF_KEYUP]
    assert len(downs) == len(ups) == 3


def test_newline_becomes_a_return_key_not_a_character(sent):
    """A literal \\n as a Unicode unit does nothing in most applications; the
    line break has to be the Return key."""
    injector.type_text("a\nb")
    returns = [e for e in sent if e.ki.wVk == injector.VK_RETURN]
    assert len(returns) == 2, "one down, one up"
    assert units(sent) == [ord("a"), ord("b")]


def test_carriage_return_does_not_double_the_line_break(sent):
    injector.type_text("a\r\nb")
    returns = [e for e in sent if e.ki.wVk == injector.VK_RETURN]
    assert len(returns) == 2


def test_empty_text_sends_nothing(sent):
    assert injector.type_text("") is True
    assert sent == []


def test_a_failed_send_is_reported(monkeypatch):
    monkeypatch.setattr(injector, "_send", lambda _events: False)
    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    monkeypatch.setattr(injector.user32, "GetAsyncKeyState", lambda _vk: 0)
    assert injector.type_text("abc") is False


def test_long_text_is_chunked_but_complete(sent):
    """Sent in batches so a huge dictation does not hand Windows one enormous
    array — every character must still arrive, exactly once."""
    text = "я" * 500
    injector.type_text(text)
    assert units(sent) == [ord("я")] * 500


def test_held_modifiers_are_released_before_typing(monkeypatch):
    """Typing "привет" while Ctrl is still down fires six shortcuts in the
    target app instead of writing a word. The hotkey is a chord, so this is
    the normal state at the moment of delivery, not an edge case."""
    captured: list = []
    monkeypatch.setattr(injector, "_send",
                        lambda events: captured.extend(events) or True)
    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    # Ctrl reads as physically held.
    monkeypatch.setattr(injector.user32, "GetAsyncKeyState",
                        lambda vk: 0x8000 if vk == injector.VK_CONTROL else 0)

    injector.type_text("a")

    first = captured[0]
    assert first.ki.wVk == injector.VK_CONTROL
    assert first.ki.dwFlags & KEYEVENTF_KEYUP, "Ctrl must be released first"


def test_nothing_is_released_when_nothing_is_held(sent):
    injector.release_held_modifiers()
    assert sent == [], "no phantom key-ups for keys the user is not holding"


# ── the clipboard route, behind FVPasteViaClipboard ──────────────────────


def test_clipboard_round_trip_and_restore():
    """The fallback delivery path. Genuinely touches the system clipboard, so
    it puts back whatever was there — a test must not eat the user's copy."""
    original = injector._clipboard_text()
    try:
        assert injector.set_clipboard_text("проверка 🙂") is True
        assert injector._clipboard_text() == "проверка 🙂"
    finally:
        if original is not None:
            injector.set_clipboard_text(original)


def test_inject_follows_the_setting(monkeypatch):
    from fortunevoice import config

    calls: list[str] = []
    monkeypatch.setattr(injector, "type_text",
                        lambda text: calls.append("typed") or True)
    monkeypatch.setattr(injector, "paste_via_clipboard",
                        lambda text: calls.append("pasted") or True)

    config.set("FVPasteViaClipboard", False)
    injector.inject("x")
    config.set("FVPasteViaClipboard", True)
    injector.inject("x")
    assert calls == ["typed", "pasted"]


# ── the clipboard route actually running ─────────────────────────────────


def test_pasting_via_the_clipboard_does_not_crash(monkeypatch):
    """FVPasteViaClipboard is the escape hatch for apps that ignore synthesized
    unicode — some Java and older Electron ones. It called `_set_clipboard_text`,
    which does not exist: the name is `set_clipboard_text`. Every dictation
    with the setting on raised NameError inside the pipeline and was filed as a
    failed transcription, so the one workaround for those apps was itself
    broken."""
    sent: list = []
    monkeypatch.setattr(injector, "_send", lambda events: sent.extend(events) or True)
    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    monkeypatch.setattr(injector.user32, "GetAsyncKeyState", lambda _vk: 0)

    # What went to the clipboard is captured rather than read back: the
    # restore thread races a read, and with sleep stubbed it wins.
    wrote: list[str] = []
    monkeypatch.setattr(injector, "set_clipboard_text",
                        lambda text: wrote.append(text) or True)
    monkeypatch.setattr(injector, "_clipboard_text", lambda: None)

    assert injector.paste_via_clipboard("через буфер") is True
    assert wrote[0] == "через буфер"
    pressed = [e.ki.wVk for e in sent]
    assert injector.VK_CONTROL in pressed and injector.VK_V in pressed


def test_the_clipboard_route_reports_a_failure_rather_than_raising(monkeypatch):
    monkeypatch.setattr(injector, "set_clipboard_text", lambda text: False)
    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    assert injector.paste_via_clipboard("x") is False


def test_a_failed_allocation_leaves_the_clipboard_alone(monkeypatch):
    """It used to call EmptyClipboard first and only then allocate, so a failed
    allocation destroyed whatever the user had copied and returned False —
    they lost their clipboard in exchange for nothing."""
    emptied: list[int] = []
    monkeypatch.setattr(injector.user32, "OpenClipboard", lambda _h: 1)
    monkeypatch.setattr(injector.user32, "CloseClipboard", lambda: 1)
    monkeypatch.setattr(injector.user32, "EmptyClipboard",
                        lambda: emptied.append(1) or 1)
    monkeypatch.setattr(injector.kernel32, "GlobalAlloc", lambda flags, size: 0)

    assert injector.set_clipboard_text("текст") is False
    assert emptied == [], "the user's clipboard must survive a failure"


# Kept alive for the whole module. `addressof(create_string_buffer(64))`
# inline hands out the address of a buffer that is garbage before the call
# returns, and `set_clipboard_text` then memmoves the text into freed memory —
# a test that corrupts the interpreter's heap to check a return value.
_SCRATCH: list = []


def _scratch() -> int:
    buffer = injector.ctypes.create_string_buffer(4096)
    _SCRATCH.append(buffer)          # outlives the call
    return injector.ctypes.addressof(buffer)


def test_a_block_that_never_reached_the_clipboard_is_freed(monkeypatch):
    """SetClipboardData failing means ownership did NOT pass. Leaving it
    unfreed leaks the allocation on every attempt."""
    freed: list[int] = []
    monkeypatch.setattr(injector.user32, "OpenClipboard", lambda _h: 1)
    monkeypatch.setattr(injector.user32, "CloseClipboard", lambda: 1)
    monkeypatch.setattr(injector.user32, "EmptyClipboard", lambda: 1)
    monkeypatch.setattr(injector.kernel32, "GlobalAlloc", lambda flags, size: 4242)
    monkeypatch.setattr(injector.kernel32, "GlobalLock", lambda h: _scratch())
    monkeypatch.setattr(injector.kernel32, "GlobalUnlock", lambda h: 1)
    monkeypatch.setattr(injector.user32, "SetClipboardData", lambda fmt, h: 0)
    monkeypatch.setattr(injector.kernel32, "GlobalFree",
                        lambda h: freed.append(h) or 0)

    assert injector.set_clipboard_text("текст") is False
    assert freed == [4242]


def test_a_block_the_clipboard_took_is_never_freed(monkeypatch):
    """Ownership passes on success. Freeing it would hand the next reader a
    dangling block."""
    freed: list[int] = []
    monkeypatch.setattr(injector.user32, "OpenClipboard", lambda _h: 1)
    monkeypatch.setattr(injector.user32, "CloseClipboard", lambda: 1)
    monkeypatch.setattr(injector.user32, "EmptyClipboard", lambda: 1)
    monkeypatch.setattr(injector.kernel32, "GlobalAlloc", lambda flags, size: 4242)
    monkeypatch.setattr(injector.kernel32, "GlobalLock", lambda h: _scratch())
    monkeypatch.setattr(injector.kernel32, "GlobalUnlock", lambda h: 1)
    monkeypatch.setattr(injector.user32, "SetClipboardData", lambda fmt, h: h)
    monkeypatch.setattr(injector.kernel32, "GlobalFree",
                        lambda h: freed.append(h) or 0)

    assert injector.set_clipboard_text("текст") is True
    assert freed == []


def test_the_clipboard_route_honours_a_per_app_profile(monkeypatch):
    """FVPasteViaClipboard is listed in profiles.OVERRIDABLE — the whole point
    is that the apps needing the clipboard route are specific ones. `inject`
    read it straight from config, so the profile was accepted, reported as
    applied, and ignored."""
    from fortunevoice import config, injector, winapi

    pasted: list[str] = []
    monkeypatch.setattr(winapi, "process_name", lambda hwnd: "OldJavaApp.exe")
    monkeypatch.setattr(injector, "paste_via_clipboard",
                        lambda text: pasted.append(text) or True)
    monkeypatch.setattr(injector, "type_text",
                        lambda text: pytest.fail("the profile asked for the clipboard"))
    config.set("FVPasteViaClipboard", False)
    config.set("FVAppProfiles", {"OldJavaApp.exe": {"FVPasteViaClipboard": True}})
    try:
        assert injector.inject("привет") is True
        assert pasted == ["привет"]
    finally:
        config.set("FVAppProfiles", {})
        config.set("FVPasteViaClipboard", False)


# ── typing or pasting ────────────────────────────────────────────────────


def _route(text_to_send, **settings):
    """The route `inject` would take, with the given settings."""
    from fortunevoice import config, injector

    config.set("FVAppProfiles", {})
    for key, value in settings.items():
        config.set(key, value)
    return injector.wants_paste(text_to_send, None)


def test_a_short_dictation_is_typed(monkeypatch):
    """Typing leaves the clipboard alone, and under a hundred characters the
    cost is imperceptible."""
    assert _route("Привет, буду через десять минут.",
                  FVDelivery="auto", FVPasteOver=120,
                  FVPasteViaClipboard=False) is False


def test_a_long_dictation_is_pasted(monkeypatch):
    """Typing costs one keystroke per character and the RECEIVING app pays it:
    measured into a bare Tk text field, 732 characters take 1.9 s to arrive,
    and an app that runs handlers per keystroke is far slower. A paste is one
    event whatever the length."""
    long_text = "Проверь последний коммит, там поменялась логика подписки. " * 4
    assert len(long_text) > 120
    assert _route(long_text, FVDelivery="auto", FVPasteOver=120,
                  FVPasteViaClipboard=False) is True


def test_always_type_is_honoured_however_long_it_is():
    """A terminal that takes Ctrl+Shift+V rather than Ctrl+V would swallow a
    paste silently."""
    long_text = "раз два три четыре пять " * 40
    assert _route(long_text, FVDelivery="type", FVPasteViaClipboard=False) is False


def test_always_paste_is_honoured_however_short_it_is():
    assert _route("да", FVDelivery="paste", FVPasteViaClipboard=False) is True


def test_the_old_boolean_still_wins():
    """FVPasteViaClipboard predates FVDelivery. Somebody who set it to true is
    someone whose app ignores synthesized input, and an upgrade must not start
    typing into it again."""
    assert _route("да", FVDelivery="type", FVPasteViaClipboard=True) is True


def test_a_profile_can_keep_one_app_on_typing():
    """The terminal case, per application."""
    from fortunevoice import config, injector

    long_text = "раз два три четыре пять " * 40
    config.set("FVDelivery", "auto")
    config.set("FVPasteViaClipboard", False)
    config.set("FVAppProfiles", {"WindowsTerminal.exe": {"FVDelivery": "type"}})
    try:
        assert injector.wants_paste(long_text, "WindowsTerminal.exe") is False
        assert injector.wants_paste(long_text, "Telegram.exe") is True
    finally:
        config.set("FVAppProfiles", {})


# ── consoles do not take Ctrl+V ──────────────────────────────────────────


def test_a_terminal_is_typed_into_however_long_the_text_is():
    """Windows terminals bind paste to Ctrl+Shift+V, or to right-click, or to
    nothing — and a paste they ignore is a dictation that goes nowhere, which
    is the one failure this app is built never to produce."""
    from fortunevoice import config, injector

    long_text = "раз два три четыре пять " * 20
    config.set("FVAppProfiles", {})
    config.set("FVDelivery", "auto")
    config.set("FVPasteViaClipboard", False)

    assert injector.wants_paste(long_text, "WindowsTerminal.exe") is False
    assert injector.wants_paste(long_text, "powershell.exe") is False
    assert injector.wants_paste(long_text, "Telegram.exe") is True


def test_the_console_list_ignores_case():
    from fortunevoice import injector

    assert injector.is_console("CMD.EXE") is True
    assert injector.is_console("cmd.exe") is True
    assert injector.is_console(None) is False


def test_asking_for_paste_in_a_terminal_still_pastes():
    """An explicit choice beats the guess: a user who set it knows something
    about their terminal that this list does not."""
    from fortunevoice import config, injector

    config.set("FVAppProfiles", {})
    config.set("FVDelivery", "paste")
    try:
        assert injector.wants_paste("короткое", "WindowsTerminal.exe") is True
    finally:
        config.set("FVDelivery", "auto")


# ── what the clipboard holds afterwards ──────────────────────────────────


def test_the_dictation_is_not_left_on_an_empty_clipboard(monkeypatch):
    """When there was nothing to put back — the clipboard was empty, or held
    an image or files, which this code cannot reproduce — the old behaviour
    left the transcript sitting there: every Ctrl+V for the rest of the day,
    and every clipboard-history tool, holding something the user said out
    loud."""
    import threading

    from fortunevoice import injector

    emptied: list[int] = []
    monkeypatch.setattr(injector, "_clipboard_text", lambda: None)
    monkeypatch.setattr(injector, "set_clipboard_text", lambda text: True)
    monkeypatch.setattr(injector, "release_held_modifiers", lambda: None)
    monkeypatch.setattr(injector, "_send", lambda events: True)
    monkeypatch.setattr(injector.user32, "GetClipboardSequenceNumber", lambda: 7)
    monkeypatch.setattr(injector.user32, "OpenClipboard", lambda handle: 1)
    monkeypatch.setattr(injector.user32, "CloseClipboard", lambda: 1)
    monkeypatch.setattr(injector.user32, "EmptyClipboard",
                        lambda: emptied.append(1) or 1)
    # The restore waits five seconds before deciding; the test does not.
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)

    assert injector.paste_via_clipboard("сказанное вслух") is True
    for thread in threading.enumerate():
        if thread.name == "clipboard-restore":
            thread.join(3)

    assert emptied == [1], "the transcript must not outlive the paste"


def test_something_copied_meanwhile_is_left_alone(monkeypatch):
    """The user copied something in those five seconds. That is theirs, and
    putting our old snapshot back would take it away from them."""
    import threading

    from fortunevoice import injector

    touched: list[str] = []
    monkeypatch.setattr(injector, "_clipboard_text", lambda: "то что было")
    monkeypatch.setattr(injector, "set_clipboard_text",
                        lambda text: touched.append(text) or True)
    monkeypatch.setattr(injector, "release_held_modifiers", lambda: None)
    monkeypatch.setattr(injector, "_send", lambda events: True)
    monkeypatch.setattr(injector.user32, "EmptyClipboard",
                        lambda: touched.append("EMPTIED") or 1)
    monkeypatch.setattr(injector.time, "sleep", lambda seconds: None)
    numbers = iter([1, 2])  # changed between the paste and the restore
    monkeypatch.setattr(injector.user32, "GetClipboardSequenceNumber",
                        lambda: next(numbers, 2))

    assert injector.paste_via_clipboard("диктовка") is True
    for thread in threading.enumerate():
        if thread.name == "clipboard-restore":
            thread.join(3)

    assert touched == ["диктовка"], touched
