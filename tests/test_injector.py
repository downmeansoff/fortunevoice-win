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
