"""Every window actually builds.

This exists because of a real failure: rewriting the palette removed
`theme.INK_RAISED`, and two windows still referenced it. Nothing caught it:
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
    skip themselves as if there were no display: a green run that tested
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
    """A glyph that raises takes the whole window build down with it, which is
    exactly how the Tk-versus-PIL arc angle bug manifested."""
    from fortunevoice.ui import icons

    for name in icons.GLYPHS:
        assert icons.image(name, 16, "#FFFFFF").size == (16, 16)
        assert icons.tile(name, 28, "#FFFFFF", "#2F7DF6").size == (28, 28)


# ── the shortcut recorder ────────────────────────────────────────────────


class _Key:
    """The two fields `_captured` reads off a Tk key event.

    Driven directly rather than through `event_generate`, which refuses letter
    keysyms without a keycode and only dispatches to mapped windows, neither
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
    """Bound on the toplevel and removed by funcid, never `bind_all`, whose
    matching `unbind_all` would wipe every other <KeyPress> handler in the
    app, not just this one."""
    widget, _saved, frame = _recorder(root)
    assert widget._binding is None

    widget._begin()
    assert widget._binding, "listening must install a binding"

    widget._end()
    assert widget._binding is None, "and ending must remove it"
    frame.destroy()


@pytest.mark.ui
def test_closing_the_window_stops_a_shortcut_recording(root):
    """The recorder holds a system-wide key-swallowing hook and has paused the
    app's hotkey. Closing the window while the chip was armed left both that
    way: the next keystroke anywhere vanished and dictation was dead, with
    nothing on screen to explain it."""
    from fortunevoice.ui import main_window as mw

    stopped = []

    class FakeCapture:
        def stop(self):
            stopped.append(1)

    window = mw.MainWindow()
    window._build()
    window._select("Settings")
    recorder = window._recorder
    recorder._listening = True
    recorder._capture = FakeCapture()

    window._on_close()

    assert stopped == [1], "the global hook must come down with the window"
    assert recorder._listening is False


# ── the keyboard the window did not have ─────────────────────────────────


@pytest.fixture
def window(root, monkeypatch):
    """A built MainWindow on the shared root."""
    from fortunevoice.ui import main_window, ui

    monkeypatch.setattr(ui, "_root", root, raising=False)
    monkeypatch.setattr(type(ui), "root", property(lambda _self: root))
    built = main_window.MainWindow()
    built._build()
    root.update_idletasks()
    root.update()
    return built


def test_ctrl_f_goes_to_the_search_box_from_any_page(window, root):
    """A list with a search field has to focus it on Ctrl+F. There were three
    key bindings in the whole UI before this, all inside the inline editor."""
    window._select("Settings")
    root.update()

    window._focus_search()
    root.update()

    assert window._page == "History", "and it switches to the page that has one"
    # focus_lastfor, not focus_get: the window is not focused at the OS
    # level in a test run, and focus_get then reports None no matter
    # what Tk was told. focus_lastfor is the widget that takes focus
    # when the toplevel gets it, which is the thing being asserted.
    assert window._window.focus_lastfor() is window._search_entry


def test_escape_closes_the_window(window, root):
    """It is a tray app: the window is opened, glanced at, and dismissed."""
    closed = []
    window._on_close = lambda: closed.append(1)
    window._bind_keys(window._window)
    # A withdrawn window drops synthetic key events on the floor.
    window._window.deiconify()
    window._window.focus_force()
    root.update()

    window._window.event_generate("<Escape>", when="now")
    root.update()

    assert closed == [1]


def test_the_window_has_the_shortcuts_a_window_is_expected_to_have(window):
    """Checked as bindings, not as events: Tk will not deliver a synthesised
    Control chord to a toplevel on this build, and asserting something the
    harness cannot produce would be a test that fails for its own reasons.
    Escape above proves the mechanism; this proves the rest are wired.

    There were three key bindings in the whole UI before this, all inside the
    inline history editor.
    """
    bound = set(window._window.bind())
    for sequence in ("<Key-Escape>", "<Control-Key-w>", "<Control-Key-f>",
                     "<Control-Key-z>", "<Control-Key-s>",
                     "<Control-Key-1>", "<Control-Key-4>"):
        assert sequence in bound, f"{sequence} is not bound"


def test_a_deleted_dictation_can_be_taken_back(window, root):
    """No confirmation on delete is the right call only if the mistake is
    recoverable, and the × is a 9 px glyph on a row whose whole surface
    copies, in a list that shifts under the cursor on every refresh."""
    from fortunevoice import metrics
    from fortunevoice.store import DictationRecord

    record = DictationRecord(date=metrics.now(), words=2, duration=1.0,
                             app="Code.exe", transcript="не удаляй меня")
    window._store.add(record)
    window._select("History")
    root.update()

    window._delete_record(window._store.all()[-1])
    root.update()
    assert not [r for r in window._store.all() if r.transcript == "не удаляй меня"]

    window._undo_delete()
    root.update()
    assert [r for r in window._store.all() if r.transcript == "не удаляй меня"]


def test_history_search_matches_the_application_too(window, root):
    """Every row shows one, the whole "where you dictate" card is built on it,
    and "that thing I dictated into Telegram" had no path but scrolling."""
    from fortunevoice import metrics
    from fortunevoice.store import DictationRecord

    window._store.add(DictationRecord(
        date=metrics.now(), words=3, duration=1.0, app="Telegram.exe",
        transcript="совершенно другие слова"))
    window._select("History")
    root.update()

    window._search_var.set("telegram")
    window._refresh_history()
    root.update()

    shown = [w for w in window._history_body.winfo_children()]
    assert shown, "the row was filtered out by its own application name"


def test_the_dictionary_example_is_not_the_users_content(window, root):
    """The page shows four example terms in a greyed-out ghost, because an
    empty borderless box does not look like an input. They must not count as
    typed: the page opened announcing unsaved changes it did not have, and
    closing it would have written the examples into the dictionary as if the
    user had asked for them."""
    window._select("Dictionary")
    root.update()

    shown = window._dictionary_text.get("1.0", "end").strip()
    assert shown, "the example has to be visible in the first place"
    assert window._dictionary_contents() == "", "but it is not content"
    assert not window._dictionary_dirty(), "and it is not an unsaved change"


def test_typing_replaces_the_dictionary_example(window, root):
    window._select("Dictionary")
    root.update()

    # focus_set is what a click does, and clearing the ghost is bound to
    # <FocusIn>. Synthesising a keypress needs a keycode this Tk build
    # will not invent for a bare keysym.
    window._dictionary_text.focus_set()
    window._dictionary_text.event_generate("<FocusIn>", when="now")
    root.update()
    window._dictionary_text.insert("1.0", "Фортуна")
    root.update()

    assert window._dictionary_contents() == "Фортуна"


def test_the_shortcut_row_shows_the_same_spelling_as_the_masthead(root):
    """The masthead renders the parsed label, "Ctrl+Alt+Space". The Settings
    row rendered the raw config string, so the one place you go to CHANGE the
    shortcut was the one place that spelled it differently, and lowercase,
    next to the words "Сочетание клавиш", reads like literal text to type."""
    from fortunevoice.hotkey import parse
    from fortunevoice.ui import widgets

    recorder = widgets.ShortcutRecorder(root, lambda: "ctrl+alt+space",
                                        lambda value: True)
    recorder.paint()
    root.update_idletasks()

    canvas = recorder.canvas
    drawn = [canvas.itemcget(item, "text") for item in canvas.find_all()
             if canvas.type(item) == "text"]

    assert parse("ctrl+alt+space").label in drawn, drawn
    assert "ctrl+alt+space" not in drawn, "the raw config string is not a label"


# -- switching tabs must not rebuild a list that has not changed ---------


def test_switching_back_to_history_does_not_rebuild_it(window, root):
    """`_select` calls the page refresh every time, and the history refresh
    destroys every card and builds them all again -- one canvas-backed row per
    dictation. The usual case is that nothing changed: the user is moving
    between tabs, not dictating."""
    from fortunevoice import metrics
    from fortunevoice.store import DictationRecord

    for index in range(5):
        window._store.add(DictationRecord(
            date=metrics.now(), words=3, duration=1.0, app="Code.exe",
            transcript=f"строка номер {index}"))
    window._select("History")
    root.update()
    drawn = window._history_body.winfo_children()
    assert drawn, "precondition: the list has rows"

    window._select("Settings")
    root.update()
    window._select("History")
    root.update()

    # The same widget objects, not rebuilt ones.
    assert window._history_body.winfo_children() == drawn


def test_a_new_dictation_still_redraws_the_list(window, root):
    from fortunevoice import metrics
    from fortunevoice.store import DictationRecord

    window._select("History")
    root.update()
    before = window._history_body.winfo_children()

    window._store.add(DictationRecord(
        date=metrics.now(), words=2, duration=1.0, app="Code.exe",
        transcript="совершенно новая запись"))
    window._refresh_history()
    root.update()

    assert window._history_body.winfo_children() != before
