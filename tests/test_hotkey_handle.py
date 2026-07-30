"""The hook's decision logic, driven with fabricated events.

No real keystrokes are involved: the test builds a KBDLLHOOKSTRUCT and calls
`_handle` directly. That covers the four rules that decide whether dictation
starts at all — swallow the chord, ignore auto-repeat, never claim the key
without its modifiers, and never react to our own typed output — without
needing a desktop session or risking input reaching another window.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice.hotkey import (  # noqa: E402
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    KBDLLHOOKSTRUCT,
    HotkeyListener,
    parse,
)

VK_SPACE = 0x20
VK_A = 0x41


def make_listener(spec_text: str = "ctrl+alt+space", modifiers_held: bool = True):
    events: list[str] = []
    listener = HotkeyListener(
        parse(spec_text),
        on_press=lambda: events.append("press"),
        on_release=lambda: events.append("release"),
    )
    listener.spec.modifiers_held = lambda: modifiers_held  # type: ignore[method-assign]
    return listener, events


def event(vk: int, flags: int = 0) -> int:
    """An lparam pointing at a KBDLLHOOKSTRUCT, as Windows would pass it."""
    struct = KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=flags, time=0, dwExtraInfo=None)
    event.keepalive.append(struct)  # the pointer must outlive the call
    return ctypes.addressof(struct)


event.keepalive = []  # type: ignore[attr-defined]


def test_press_and_release_fire_and_are_swallowed():
    listener, events = make_listener()
    assert listener._handle(WM_KEYDOWN, event(VK_SPACE)) is True
    assert listener._handle(WM_KEYUP, event(VK_SPACE)) is True
    assert events == ["press", "release"]


def test_auto_repeat_is_swallowed_without_refiring():
    """Holding the key sends a stream of WM_KEYDOWN. Dictation must start once
    and the repeats must still not reach the focused app."""
    listener, events = make_listener()
    listener._handle(WM_KEYDOWN, event(VK_SPACE))
    for _ in range(5):
        assert listener._handle(WM_KEYDOWN, event(VK_SPACE)) is True
    assert events == ["press"]


def test_key_without_modifiers_passes_through():
    """Plain Space must stay plain Space — not swallowed, no dictation."""
    listener, events = make_listener(modifiers_held=False)
    assert listener._handle(WM_KEYDOWN, event(VK_SPACE)) is False
    assert events == []


def test_release_without_a_press_passes_through():
    """The chord's modifiers were released first, so we never saw the press —
    the key-up belongs to the app, not to us."""
    listener, events = make_listener()
    assert listener._handle(WM_KEYUP, event(VK_SPACE)) is False
    assert events == []


def test_other_keys_are_untouched():
    listener, events = make_listener()
    assert listener._handle(WM_KEYDOWN, event(VK_A)) is False
    assert events == []


def test_injected_events_are_ignored():
    """Our own typed dictation comes back through the hook. Reacting to it
    would let a transcript containing the trigger key restart dictation."""
    listener, events = make_listener()
    assert listener._handle(WM_KEYDOWN, event(VK_SPACE, LLKHF_INJECTED)) is False
    assert events == []


def test_syskey_variants_are_handled():
    """Alt is part of the default chord, so Windows delivers WM_SYSKEYDOWN /
    WM_SYSKEYUP rather than the plain messages."""
    listener, events = make_listener()
    assert listener._handle(WM_SYSKEYDOWN, event(VK_SPACE)) is True
    assert listener._handle(WM_SYSKEYUP, event(VK_SPACE)) is True
    assert events == ["press", "release"]


def test_modifier_only_trigger():
    """FVHotkey = "rctrl" — the trigger is itself a modifier, so no additional
    modifiers are required."""
    listener, events = make_listener("rctrl")
    assert listener._handle(WM_KEYDOWN, event(0xA3)) is True
    assert listener._handle(WM_KEYUP, event(0xA3)) is True
    assert events == ["press", "release"]
