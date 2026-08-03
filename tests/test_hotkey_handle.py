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
import time

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
    listener.spec.modifiers_held = lambda *a: modifiers_held  # type: ignore[method-assign]
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


# ── modifier triggers: "hold Ctrl+Alt to talk" ───────────────────────────
#
# Two rules make these safe. The key is never swallowed, because eating Ctrl
# would break Ctrl+C everywhere. And the press only counts after HOLD_SECONDS,
# because Ctrl is also the first half of Ctrl+C and Ctrl+Alt is what a Russian
# layout's AltGr sends — a tap has to do nothing at all.

VK_LCONTROL, VK_RCONTROL, VK_LMENU = 0xA2, 0xA3, 0xA4


def hold(listener, vk: int) -> bool:
    """Press, wait past the threshold, release. Returns what the hook returned."""
    swallowed = listener._handle(WM_KEYDOWN, event(vk))
    time.sleep(listener.HOLD_SECONDS + 0.15)
    listener._handle(WM_KEYUP, event(vk))
    time.sleep(0.05)
    return swallowed


def test_modifier_only_trigger():
    """FVHotkey = "rctrl" — the trigger is itself a modifier, so no additional
    modifiers are required."""
    listener, events = make_listener("rctrl")
    assert hold(listener, VK_RCONTROL) is False, "a modifier must reach the app"
    assert events == ["press", "release"]


def test_modifier_trigger_is_never_swallowed():
    """The whole point: Ctrl still has to work as Ctrl while it doubles as the
    push-to-talk key."""
    listener, _ = make_listener("ctrl")
    assert listener._handle(WM_KEYDOWN, event(VK_LCONTROL)) is False
    assert listener._handle(WM_KEYDOWN, event(VK_LCONTROL)) is False  # auto-repeat
    assert listener._handle(WM_KEYUP, event(VK_LCONTROL)) is False


def test_a_tap_on_a_modifier_starts_nothing():
    """Ctrl+C begins with a Ctrl press. Released before the threshold, it must
    not produce a dictation — not even an empty one."""
    listener, events = make_listener("ctrl")
    listener._handle(WM_KEYDOWN, event(VK_LCONTROL))
    listener._handle(WM_KEYUP, event(VK_LCONTROL))
    time.sleep(listener.HOLD_SECONDS + 0.2)
    assert events == []


def test_either_side_of_a_modifier_works():
    """A low-level hook reports VK_LCONTROL/VK_RCONTROL, never the combined
    VK_CONTROL, so "ctrl" has to match both hands."""
    for vk in (VK_LCONTROL, VK_RCONTROL):
        listener, events = make_listener("ctrl")
        hold(listener, vk)
        assert events == ["press", "release"], hex(vk)


def test_modifier_chord_needs_every_key_held():
    """"ctrl+alt" with Ctrl not actually down is a plain Alt — not our chord."""
    listener, events = make_listener("ctrl+alt", modifiers_held=False)
    hold(listener, VK_LMENU)
    assert events == []


def test_ordinary_chord_still_fires_immediately():
    """The threshold applies to modifier triggers only; Ctrl+Alt+Space must not
    lose its first 300 ms of speech."""
    listener, events = make_listener("ctrl+alt+space")
    listener._handle(WM_KEYDOWN, event(VK_SPACE))
    assert events == ["press"]
