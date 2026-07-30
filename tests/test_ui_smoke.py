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


@pytest.fixture()
def root():
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
