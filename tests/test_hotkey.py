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
    # Right Ctrl is still a Ctrl: swallowing it would break Ctrl+C.
    assert spec.modifier_trigger is True


def test_bare_modifier_as_trigger():
    """"ctrl" on its own. A low-level hook reports the two sides separately, so
    both have to be accepted or the hotkey works with one hand only."""
    spec = parse("ctrl")
    assert spec.modifier_trigger is True
    assert spec.modifiers == ["ctrl"]
    assert set(spec.keys) == {0xA2, 0xA3}


def test_modifier_chord_accepts_either_order():
    """"ctrl+alt" — every key is both a trigger and a requirement, so it fires
    whichever finger lands second and ends when either one lifts."""
    spec = parse("ctrl+alt")
    assert spec.modifier_trigger is True
    assert spec.modifiers == ["ctrl", "alt"]
    assert set(spec.keys) == {0xA2, 0xA3, 0xA4, 0xA5}
    assert spec.label == "Ctrl+Alt"


def test_ordinary_key_is_not_a_modifier_trigger():
    for text in ("f9", "ctrl+alt+space", "ctrl+d"):
        assert parse(text).modifier_trigger is False, text


def test_the_arriving_key_is_not_checked_against_the_async_state(monkeypatch):
    """A low-level hook runs before Windows commits the keystroke, so
    GetAsyncKeyState still reports the key being pressed as up. Checking it
    there made Ctrl+Alt never fire once — nothing is held in this process, and
    the chord must still be considered satisfied by the key the hook reports."""
    from fortunevoice import hotkey as H

    # Nothing down, stated rather than assumed: the second assertion used
    # to ask the real keyboard, so the test failed whenever Ctrl happened
    # to be held while the suite ran.
    monkeypatch.setattr(H, "_key_is_down", lambda vk: False)
    spec = parse("ctrl")
    assert spec.modifiers_held(0xA2) is True   # "Ctrl just arrived"
    assert spec.modifiers_held() is False      # nothing is actually down


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
    from fortunevoice.hotkey import ChordCapture

    assert [name for name, _ in ChordCapture._MODIFIER_ORDER] == [
        "ctrl", "alt", "shift", "win"]

    capture = ChordCapture(lambda chord: None)
    # Pressed shift-first, still reported ctrl-first.
    held = {0xA0, 0xA2, 0xA4}  # LSHIFT, LCONTROL, LMENU
    assert capture._modifier_chord(held) == "ctrl+alt+shift"
    assert parse(capture._modifier_chord(held)).modifier_trigger is True


def test_capture_reports_the_whole_chord_even_when_released_out_of_order():
    """Lifting Alt first out of Ctrl+Alt must still record "ctrl+alt" — the
    chord is read from what was down at the moment of release, not from what
    happens to remain."""
    from fortunevoice.hotkey import ChordCapture

    capture = ChordCapture(lambda chord: None)
    assert capture._modifier_chord({0xA2, 0xA4}) == "ctrl+alt"
    assert capture._modifier_chord(set()) == ""


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


# ── a key-up the hook never saw ──────────────────────────────────────────


def _listener_holding(monkeypatch, spec_text="ctrl+alt+space"):
    from fortunevoice import hotkey as H

    listener = H.HotkeyListener(H.parse(spec_text), lambda: None, lambda: None)
    listener._held = True
    return listener


def test_a_release_lost_to_a_desktop_switch_is_recovered(monkeypatch):
    """A UAC prompt, Win+L and Ctrl+Alt+Del switch desktops, and the key-up
    lands on a desktop this hook is not on. The latch then says the key is
    still down: the next press is read as auto-repeat and swallowed, and a
    recording that was running never ends — it runs to the 300 s cap and types
    five minutes of room noise."""
    from fortunevoice import hotkey as H

    released: list[int] = []
    listener = _listener_holding(monkeypatch)
    monkeypatch.setattr(listener, "_end_press", lambda: released.append(1))
    # The helper, not the ctypes pointer: restoring an attribute on the
    # shared user32 object drops the argtypes declared at import.
    monkeypatch.setattr(H, "_key_is_down", lambda vk: False)

    listener._resync_held()

    assert listener._held is False
    assert released == [1], "the press has to be ended, not just forgotten"


def test_a_key_that_is_genuinely_still_down_is_left_alone(monkeypatch):
    """The resync must not cut a dictation short while the user is still
    holding the key."""
    from fortunevoice import hotkey as H

    released: list[int] = []
    listener = _listener_holding(monkeypatch)
    monkeypatch.setattr(listener, "_end_press", lambda: released.append(1))
    monkeypatch.setattr(H, "_key_is_down", lambda vk: True)

    listener._resync_held()

    assert listener._held is True
    assert released == []


# ── a hook Windows removed without telling anyone ────────────────────────


def _listener(monkeypatch, spec_text="ctrl+alt"):
    from fortunevoice import hotkey as H

    return H.HotkeyListener(H.parse(spec_text), lambda: None, lambda: None)


def test_a_hook_that_ignores_its_own_probe_is_reinstalled(monkeypatch):
    """Windows removes a low-level hook whose callback runs too long and says
    nothing about it: no error, no final callback, no API to ask. Twice on
    this machine the app was running, the log said "hotkey listening", and the
    shortcut did nothing until it was restarted by hand.

    So the hook is asked rather than guessed about: a key nobody uses is
    pressed, and a hook that does not report it is not listening.
    """
    import time

    from fortunevoice import hotkey as H

    listener = _listener(monkeypatch)
    probes = []
    monkeypatch.setattr(H.winapi, "tap_probe_key", lambda: probes.append(1))
    listener._last_seen = time.monotonic() - 60

    listener._check_still_hooked()          # asks
    assert probes == [1], "the probe has to actually be sent"
    assert listener._reinstall is False, "and the verdict waits for the answer"

    listener._check_still_hooked()          # no answer came
    assert listener._reinstall is True


def test_a_hook_that_answers_its_probe_is_left_alone(monkeypatch):
    """The reply is an ordinary hook callback, which stamps `_last_seen`."""
    import time

    from fortunevoice import hotkey as H

    listener = _listener(monkeypatch)
    monkeypatch.setattr(H.winapi, "tap_probe_key",
                        lambda: setattr(listener, "_last_seen", time.monotonic()))
    listener._last_seen = time.monotonic() - 60

    listener._check_still_hooked()
    listener._check_still_hooked()

    assert listener._reinstall is False


def test_moving_the_mouse_is_not_a_dead_hook(monkeypatch):
    """The first version of this compared `GetLastInputInfo` against what the
    hook had seen — and that counts the MOUSE. Moving the pointer without
    typing looked exactly like a dead hook, so it was reinstalled every twenty
    seconds for as long as the machine was in use. The probe does not care what
    the mouse is doing."""
    import time

    from fortunevoice import hotkey as H

    listener = _listener(monkeypatch)
    monkeypatch.setattr(H.winapi, "tap_probe_key",
                        lambda: setattr(listener, "_last_seen", time.monotonic()))
    listener._last_seen = time.monotonic() - 60

    for _ in range(6):
        listener._check_still_hooked()

    assert listener._reinstall is False


def test_a_recently_used_hook_is_not_probed(monkeypatch):
    """No question worth asking while keys are arriving — and the probe is a
    synthetic keypress, which is not free."""
    import time

    from fortunevoice import hotkey as H

    listener = _listener(monkeypatch)
    probes = []
    monkeypatch.setattr(H.winapi, "tap_probe_key", lambda: probes.append(1))
    listener._last_seen = time.monotonic()

    listener._check_still_hooked()

    assert probes == []
    assert listener._reinstall is False


def test_a_probe_that_cannot_be_sent_proves_nothing(monkeypatch):
    """SendInput can be refused — by UIPI, by a full input queue. Treating
    "could not ask" as "broken" would reinstall the hook on a timer for ever."""
    import time

    from fortunevoice import hotkey as H

    listener = _listener(monkeypatch)

    def refuse():
        raise OSError("SendInput refused")

    monkeypatch.setattr(H.winapi, "tap_probe_key", refuse)
    listener._last_seen = time.monotonic() - 60

    listener._check_still_hooked()
    listener._check_still_hooked()

    assert listener._reinstall is False


# -- the probe is real input, so it may not be sent to an empty desk ------


def _deaf_listener(monkeypatch, idle_ms, sent_ago=1000.0):
    """A listener whose hook has heard nothing for a minute."""
    import time

    from fortunevoice import hotkey as H

    listener = H.HotkeyListener(H.parse("ctrl+alt"), lambda: None, lambda: None)
    listener._last_seen = time.monotonic() - 60
    listener._probe_at = 0.0
    listener._probe_sent_at = time.monotonic() - sent_ago
    monkeypatch.setattr(H.winapi, "milliseconds_since_last_input", lambda: idle_ms)
    return listener


def test_an_idle_machine_is_never_probed(monkeypatch):
    """The probe presses a key, and Windows counts that as somebody being at
    the desk: the screen never blanks, the screensaver never starts, "lock
    after N minutes" never fires, the machine never sleeps, and a laptop runs
    the night out. Sending one every twenty seconds of quiet turned the app
    into a mouse jiggler nobody asked for."""
    from fortunevoice import hotkey as H

    probes = []
    monkeypatch.setattr(H.winapi, "tap_probe_key", lambda: probes.append(1))
    listener = _deaf_listener(monkeypatch, idle_ms=600_000.0)

    listener._check_still_hooked()

    assert probes == [], "nobody is here; there is nothing to prove"
    assert listener._reinstall is False


def test_a_machine_in_use_is_probed(monkeypatch):
    """Somebody is at the mouse but has not typed for a minute -- which is
    exactly what a dead hook looks like, and the only way to tell is to ask."""
    from fortunevoice import hotkey as H

    probes = []
    monkeypatch.setattr(H.winapi, "tap_probe_key", lambda: probes.append(1))
    listener = _deaf_listener(monkeypatch, idle_ms=300.0)

    listener._check_still_hooked()

    assert probes == [1]
    assert listener._probe_at, "and the answer is expected on the next tick"


def test_the_probe_does_not_answer_its_own_question(monkeypatch):
    """Our own probe shows up in GetLastInputInfo as recent input. Treating
    that as the user being present would keep the machine awake by itself,
    which is the whole bug."""
    from fortunevoice import hotkey as H

    probes = []
    monkeypatch.setattr(H.winapi, "tap_probe_key", lambda: probes.append(1))
    listener = _deaf_listener(monkeypatch, idle_ms=300.0, sent_ago=1.0)

    listener._check_still_hooked()

    assert probes == []


def test_a_probe_nobody_answered_reinstalls_the_hook(monkeypatch):
    """The point of the whole mechanism."""
    import time

    from fortunevoice import hotkey as H

    listener = _deaf_listener(monkeypatch, idle_ms=300.0)
    listener._probe_at = time.monotonic() - 1

    listener._check_still_hooked()

    assert listener._reinstall is True
