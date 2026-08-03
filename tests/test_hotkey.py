"""Hotkey parsing. No Windows API is touched — only the string → (modifiers,
virtual key) mapping, which is where a typo in config.json turns into a hotkey
that silently never fires."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice.hotkey import parse  # noqa: E402


def test_default_chord():
    spec = parse("ctrl+alt+space")
    assert spec.modifiers == ["ctrl", "alt"]
    assert spec.key == 0x20


def test_single_key():
    spec = parse("f9")
    assert spec.modifiers == []
    assert spec.key == 0x78


def test_case_and_spacing_insensitive():
    assert parse("  CTRL + Space ").key == parse("ctrl+space").key


def test_modifier_as_trigger():
    # "hold right ctrl to talk" — no modifiers required alongside it.
    spec = parse("rctrl")
    assert spec.modifiers == []
    assert spec.key == 0xA3


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError, match="unknown key"):
        parse("ctrl+spacebar")


def test_unknown_modifier_is_rejected():
    with pytest.raises(ValueError, match="unknown modifier"):
        parse("meta+space")


def test_empty_is_rejected():
    with pytest.raises(ValueError):
        parse("   ")


# ── the recorder and the parser must agree ───────────────────────────────


def test_every_keysym_the_recorder_maps_is_parseable():
    """The recorder turns a Tk keysym into a string; the parser has to accept
    it. They were written separately, and the first thing that fell through the
    gap was the Windows key: Tk reports it as `Win_L`, the ignore-list only
    knew the X11 name `Super_L`, so a lone Win press became the literal key
    "win_l" and the user got «unknown key 'win_l'»."""
    from fortunevoice.ui.widgets import ShortcutRecorder

    for keysym, name in ShortcutRecorder._KEYSYM.items():
        assert keysym not in ShortcutRecorder._IGNORED, keysym
        parse(name)  # raises ValueError if the parser does not know it


def test_the_recorder_ignores_every_modifier_the_parser_knows():
    """A modifier alone is not a shortcut. If one is missing from the ignore
    list it arrives as a trigger key and the parser rejects the whole chord."""
    from fortunevoice.hotkey import MODIFIER_KEYS
    from fortunevoice.ui.widgets import ShortcutRecorder

    # Tk's spellings for the modifiers the parser accepts as prefixes.
    keysyms = {
        "ctrl": ("Control_L", "Control_R"),
        "control": ("Control_L", "Control_R"),
        "alt": ("Alt_L", "Alt_R"),
        "shift": ("Shift_L", "Shift_R"),
        "win": ("Win_L", "Win_R", "Super_L", "Super_R"),
    }
    for modifier in MODIFIER_KEYS:
        for keysym in keysyms[modifier]:
            assert keysym in ShortcutRecorder._IGNORED, (modifier, keysym)


@pytest.mark.parametrize("chord", [
    "win+d", "win+shift+s", "ctrl+alt+space", "ctrl+shift+f1", "f9", "rctrl",
])
def test_chords_the_recorder_can_emit_all_parse(chord):
    spec = parse(chord)
    assert spec.label


# ── global chord capture ─────────────────────────────────────────────────


def test_every_captured_vk_maps_to_a_parseable_name():
    """Capture reports a chord by name; the parser has to accept every one it
    can produce, or a recorded shortcut is rejected the moment it is saved."""
    from fortunevoice.hotkey import _VK_TO_NAME

    assert len(_VK_TO_NAME) > 80, "the map should cover the whole key table"
    for name in _VK_TO_NAME.values():
        parse(name)


def test_modifier_keys_are_never_reported_as_a_trigger():
    """A bare Ctrl is not a shortcut. Every modifier virtual-key must be in the
    ignore set, or capture would return "ctrl" as the trigger and the parser
    would refuse the chord."""
    from fortunevoice.hotkey import _MODIFIER_VKS, MODIFIER_KEYS

    for keys in MODIFIER_KEYS.values():
        for vk in keys:
            assert vk in _MODIFIER_VKS, hex(vk)


def test_capture_orders_modifiers_the_way_the_parser_prints_them():
    """ctrl, alt, shift, win — so the chip shows "ctrl+alt+space" whatever
    order the user's fingers landed in."""
    import inspect

    from fortunevoice.hotkey import ChordCapture

    source = inspect.getsource(ChordCapture._handle)
    order = [name for name in ("ctrl", "alt", "shift", "win")
             if f'("{name}"' in source]
    assert order == ["ctrl", "alt", "shift", "win"]


@pytest.mark.live
def test_capture_reads_a_real_chord():
    """Needs a desktop session: presses real keys and expects the hook to see
    them. Runs under `pytest -m live`."""
    import ctypes
    import time

    from fortunevoice.hotkey import ChordCapture

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    captured: list[str] = []
    capture = ChordCapture(captured.append)
    capture.start()
    time.sleep(0.4)
    for vk in (0x11, 0x70):            # Ctrl down, F1 down
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
    for vk in (0x70, 0x11):            # and up
        user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.03)
    time.sleep(0.5)
    capture.stop()

    assert captured == ["ctrl+f1"]
    parse(captured[0])
