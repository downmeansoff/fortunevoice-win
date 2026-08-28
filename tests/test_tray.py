"""The tray menu.

The menu is a tree of lambdas — `enabled=`, `visible=`, `checked=`, and text
callables that pystray evaluates when the user right-clicks. None of them had
ever been executed by a test, so a name that no longer existed would surface
as a menu that fails to open, on a right-click, in front of the user.

pystray is never started here: the menu is built and every callable in it is
called, which is what a right-click does.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice.app import App, State  # noqa: E402
from fortunevoice.tray import Tray, _icon_image  # noqa: E402


@pytest.fixture
def tray():
    return Tray(App())


def walk(menu):
    """Every item in the menu, descending into submenus."""
    for item in menu:
        yield item
        submenu = getattr(item, "submenu", None)
        if submenu:
            yield from walk(submenu)


def test_every_menu_label_survives_being_evaluated(tray):
    """Right-clicking the tray evaluates all of these. One stale attribute
    reference and the menu does not open at all.

    Renamed: this exercises the LABELS. What happens when an item is actually
    clicked is the test below, which the old name claimed to cover.
    """
    items = list(walk(tray._menu()))
    assert len(items) > 5, "the menu should not be almost empty"
    for item in items:
        str(item.text)          # text may itself be a callable
        assert item.enabled in (True, False)
        assert item.visible in (True, False)
        item.checked            # None for anything that is not a checkbox


def test_the_menu_reflects_having_a_last_dictation(tray):
    """Copy and retype are disabled until there is something to act on —
    offering them with nothing behind them looks like a broken app."""
    def state():
        return {str(i.text): i.enabled for i in walk(tray._menu())}

    tray.app.last_transcript = ""
    before = state()
    tray.app.last_transcript = "проверка"
    after = state()

    changed = [text for text in before if before[text] != after.get(text)]
    assert len(changed) == 2, f"copy and retype should both flip, got {changed}"


def test_retype_is_offered(tray):
    """The rescue for a dictation typed into the wrong window."""
    tray.app.last_transcript = "проверка"
    labels = [str(i.text) for i in walk(tray._menu())]
    assert any("апечатать" in label for label in labels), labels


def test_retype_does_not_type_when_there_is_nothing(tray, monkeypatch):
    from fortunevoice import injector

    monkeypatch.setattr(injector, "inject",
                        lambda _t: pytest.fail("nothing should be typed"))
    tray.app.last_transcript = ""
    tray._retype_last()


def test_an_icon_exists_for_every_state():
    """A state with no colour would fall back to the idle tint and the tray
    would silently stop reporting what the app is doing."""
    for state in State:
        image = _icon_image(state)
        assert image.size == (64, 64)
    # Recording and idle must not look the same.
    assert _icon_image(State.RECORDING).tobytes() != _icon_image(State.IDLE).tobytes()


def test_the_level_meter_changes_the_icon():
    """The bars either side of the microphone are what tell the user their
    microphone is actually picking something up."""
    quiet = _icon_image(State.RECORDING, level=0.0)
    loud = _icon_image(State.RECORDING, level=1.0)
    assert quiet.tobytes() != loud.tobytes()


# ── a window that fails to build must not fail in silence ────────────────


def test_a_failing_ui_callback_reaches_the_user(monkeypatch):
    """Twice during this port a window silently did not appear — a renamed
    colour constant, and a missing helper. Both were found by reading the log.
    The user clicked "Settings" and nothing happened, with no other sign."""
    from fortunevoice.ui import ui

    app = App()
    said: list[tuple[str, str]] = []
    app.on_notify = lambda title, body: said.append((title, body))
    monkeypatch.setattr(ui, "on_error", app._on_ui_error, raising=False)

    ui.on_error("AttributeError: module has no attribute 'INK_RAISED'")
    assert said, "the failure must be reported, not only logged"
    assert "INK_RAISED" in said[0][1]


def test_the_same_failure_is_reported_only_once():
    """A repaint that throws on every frame would otherwise become a stream of
    notifications."""
    app = App()
    said: list[tuple[str, str]] = []
    app.on_notify = lambda title, body: said.append((title, body))

    for _ in range(5):
        app._on_ui_error("TypeError: the same one every frame")
    app._on_ui_error("ValueError: a different one")
    assert len(said) == 2


def test_the_pump_itself_reports_a_raising_job():
    """Not the wrapper — the real queue drain. A test that only called
    `ui.on_error` would pass even if `_pump` never invoked it, which is
    precisely the wiring that was missing before."""
    from fortunevoice.ui import UiThread

    class FakeRoot:
        def after(self, _ms, _fn):
            pass

        def quit(self):
            pass

    thread = UiThread()
    thread._root = FakeRoot()
    seen: list[str] = []
    thread.on_error = seen.append

    def explode():
        raise RuntimeError("the window did not build")

    thread._queue.put(explode)
    thread._pump()

    assert seen == ["RuntimeError: the window did not build"]


# ── what the app costs while doing nothing ───────────────────────────────


class RecordingRoot:
    """A Tk root that only remembers the delay it was asked to wait."""

    def __init__(self):
        self.delays: list[int] = []

    def after(self, ms, _fn):
        self.delays.append(ms)

    def quit(self):
        pass


def test_the_pump_slows_down_once_nothing_is_happening():
    """Measured: draining an empty queue forty times a second costs 1.4% of a
    core, all day, for a tray app doing nothing — the largest thing this
    process burns while idle. Backing off took it to 0.6%."""
    from fortunevoice.ui import _IDLE_AFTER_EMPTY, _IDLE_PUMP_MS, _PUMP_MS, UiThread

    thread = UiThread()
    thread._root = RecordingRoot()
    for _ in range(_IDLE_AFTER_EMPTY + 5):
        thread._pump()

    assert thread._root.delays[0] == _PUMP_MS, "fast while it might still be busy"
    assert thread._root.delays[-1] == _IDLE_PUMP_MS, "slow once plainly idle"


def test_one_job_puts_it_straight_back_to_the_fast_rate():
    """A dictation's overlay and waveform must never be the slow ones — the
    pill appearing late reads as the app not having heard the key."""
    from fortunevoice.ui import _IDLE_AFTER_EMPTY, _PUMP_MS, UiThread

    thread = UiThread()
    thread._root = RecordingRoot()
    for _ in range(_IDLE_AFTER_EMPTY + 5):
        thread._pump()

    thread._queue.put(lambda: None)
    thread._pump()
    assert thread._root.delays[-1] == _PUMP_MS


def test_the_idle_rate_stays_under_what_the_hotkey_already_spends():
    """The pill can be at most one pump late. That has to disappear next to
    the 300 ms the hotkey already spends deciding a modifier chord was held,
    or the back-off would be visible as lag."""
    from fortunevoice.hotkey import HotkeyListener
    from fortunevoice.ui import _IDLE_PUMP_MS

    assert _IDLE_PUMP_MS < HotkeyListener.HOLD_SECONDS * 1000 / 2


def test_every_menu_item_survives_being_clicked(tray, monkeypatch):
    """The labels are evaluated when the menu opens; the actions run when
    something is chosen. A stale attribute in one of those is invisible until
    a user clicks it, and then the tray looks like it did nothing.

    Clicked in both states, because pystray does not run a disabled item's
    action at all: "Retry model" is enabled only after a failure, so a pass
    over the idle menu leaves exactly the handlers a user reaches when
    something is already wrong untested.
    """
    from fortunevoice.app import State

    called: list[str] = []
    for name in ("open_main_window", "show_onboarding", "open_data_folder",
                 "reload_model", "recover_failed", "retype_last", "quit",
                 "copy_last"):
        if hasattr(tray.app, name):
            monkeypatch.setattr(tray.app, name,
                                lambda *a, _n=name, **k: called.append(_n))

    def click_everything() -> int:
        clicked = 0
        for item in walk(tray._menu()):
            # A pystray MenuItem IS the callable: item(icon) runs its action,
            # and does nothing at all when the item is disabled.
            if item.submenu is not None or not item.enabled:
                continue
            item(None)
            clicked += 1
        return clicked

    total = click_everything()

    # Now the states that unlock the rest of the menu.
    tray.app._set_state(State.ERROR, "Model failed to load")
    tray.app.last_transcript = "привет"
    monkeypatch.setattr(tray.app.recovery, "pending", lambda: ["one.wav"])
    total += click_everything()

    assert total > 8, f"only {total} items were actually clicked"
    assert "reload_model" in called, "the failure-state items were never reached"
