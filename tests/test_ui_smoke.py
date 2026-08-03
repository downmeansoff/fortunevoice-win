"""Every window actually builds.

This exists because of a real failure: rewriting the palette removed
`theme.INK_RAISED`, and two windows still referenced it. Nothing caught it —
the main window was screenshotted and looked perfect, while the first-run
screen raised `AttributeError` inside a UI callback, was swallowed by the
event pump, and simply never appeared. The user would have launched the app
and seen nothing at all.

So this constructs each window for real, on a real Tk root, and fails if any
of them raises. It is opt-in (`-m ui`) because it needs a desktop session, but
it is cheap enough to run before every commit that touches ui/.

It deliberately does NOT assert on appearance. Looks are checked by eye from
screenshots; this checks the thing screenshots cannot, which is that a window
exists at all.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]


@pytest.fixture(scope="module")
def root():
    """ONE Tk root for the whole module, which is what the app itself does.

    Creating and destroying a root per test breaks Tcl after a couple of
    cycles ("invalid command name tcl_findLibrary") and the remaining tests
    skip themselves as if there were no display — a green run that tested
    nothing.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        widget = tkinter.Tk()
    except tkinter.TclError as exc:  # pragma: no cover - headless agent
        pytest.skip(f"no display: {exc}")
    widget.withdraw()
    try:
        yield widget
    finally:
        widget.destroy()


def _drain(root) -> None:
    root.update_idletasks()
    root.update()


def test_main_window_builds_every_page(root, monkeypatch):
    from fortunevoice.ui import main_window, ui

    monkeypatch.setattr(ui, "_root", root, raising=False)
    monkeypatch.setattr(type(ui), "root", property(lambda _self: root))

    window = main_window.MainWindow()
    window._build()
    _drain(root)
    for name, _glyph in main_window.PAGES:
        window._select(name)
        _drain(root)
    assert window._window.winfo_exists()


def test_onboarding_builds(root, monkeypatch):
    from fortunevoice.ui import onboarding, ui

    monkeypatch.setattr(type(ui), "root", property(lambda _self: root))
    screen = onboarding.Onboarding()
    screen._build()
    _drain(root)
    assert screen._window.winfo_exists()


def test_result_panel_builds_and_shows(root, monkeypatch):
    from fortunevoice.ui import result, ui

    monkeypatch.setattr(type(ui), "root", property(lambda _self: root))
    panel = result.ResultPanel()
    panel._show("раз два три", "You switched windows")
    _drain(root)
    assert panel._window.winfo_exists()
    panel._hide()


def test_pill_builds_in_every_mode(root, monkeypatch):
    from fortunevoice.ui import pill as pill_module
    from fortunevoice.ui import ui

    monkeypatch.setattr(type(ui), "root", property(lambda _self: root))
    pill = pill_module.Pill()
    for mode in ("recording", "processing", "no-signal", "error"):
        pill._show(mode)
        pill.push_level(0.15)
        pill._draw()
        _drain(root)
    pill._hide_after = None
    pill._hide()


def test_every_glyph_renders():
    """A glyph that raises takes the whole window build down with it — which is
    exactly how the Tk-versus-PIL arc angle bug manifested."""
    from fortunevoice.ui import icons

    for name in icons.GLYPHS:
        assert icons.image(name, 16, "#FFFFFF").size == (16, 16)
        assert icons.tile(name, 28, "#FFFFFF", "#2F7DF6").size == (28, 28)


# ── the shortcut recorder ────────────────────────────────────────────────


class _Key:
    """The two fields `_captured` reads off a Tk key event.

    Driven directly rather than through `event_generate`, which refuses letter
    keysyms without a keycode and only dispatches to mapped windows — neither
    of which is what this logic is about.
    """

    def __init__(self, keysym: str) -> None:
        self.keysym = keysym
        self.state = 0


def _recorder(root, get="ctrl+alt+space", on_listening=None):
    import tkinter as tk

    from fortunevoice.ui import widgets

    saved: list[str] = []
    frame = tk.Toplevel(root)
    frame.geometry("300x120+80+80")
    widget = widgets.ShortcutRecorder(
        frame, lambda: get, lambda v: (saved.append(v), True)[1],
        on_listening=on_listening)
    widget.pack()
    root.update()
    return widget, saved, frame


def test_recorder_captures_a_chord(root):
    widget, saved, frame = _recorder(root)
    widget._begin()
    widget._captured(_Key("F9"))
    assert saved == ["f9"]
    assert not widget._listening, "capturing one chord ends the session"
    widget._end()
    frame.destroy()


def test_recorder_pauses_the_global_hotkey_while_listening(root):
    """The chord a user reaches for first is the one already configured. Left
    live, the hook would swallow it and start a dictation instead of recording
    the key."""
    events: list[bool] = []
    widget, _saved, frame = _recorder(root, on_listening=events.append)

    widget._begin()
    assert events == [True], "paused before any key can arrive"
    widget._captured(_Key("F9"))
    assert events == [True, False], "and resumed as soon as capture ends"
    widget._end()
    frame.destroy()


def test_recorder_ignores_a_modifier_on_its_own(root):
    widget, saved, frame = _recorder(root)
    widget._begin()
    for modifier in ("Control_L", "Alt_R", "Shift_L", "Super_L"):
        widget._captured(_Key(modifier))
    assert saved == [], "Ctrl alone is not a shortcut"
    assert widget._listening, "and it keeps waiting for a real key"
    widget._end()
    frame.destroy()


def test_escape_cancels_without_saving(root):
    widget, saved, frame = _recorder(root)
    widget._begin()
    widget._captured(_Key("Escape"))
    assert saved == []
    assert not widget._listening
    widget._end()
    frame.destroy()


def test_the_key_binding_is_scoped_and_removed(root):
    """Bound on the toplevel and removed by funcid — never `bind_all`, whose
    matching `unbind_all` would wipe every other <KeyPress> handler in the
    app, not just this one."""
    widget, _saved, frame = _recorder(root)
    assert widget._binding is None

    widget._begin()
    assert widget._binding, "listening must install a binding"

    widget._end()
    assert widget._binding is None, "and ending must remove it"
    frame.destroy()
