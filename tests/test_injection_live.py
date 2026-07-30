"""A real SendInput round trip.

Typing text into another process is the part of this port with the most ways
to be silently wrong — surrogate pairs, newlines, held modifiers, dropped
characters under a fast burst — and none of them show up in a unit test of the
encoding helpers.

So this drives the real Win32 path: it opens a Tk window **we own**, focuses
it, types through `injector.type_text`, and reads the characters back out of
the widget.

**Never type without proving we own the foreground window.** Windows refuses
`SetForegroundWindow` from a process that is not already in front, and Tk's
`focus_force()` does not report that refusal — it just leaves the window
topmost but not focused. An earlier version of this test trusted it and typed
its Cyrillic samples straight into the terminal that launched pytest. Every
case now checks `GetForegroundWindow()` belongs to this process and skips
rather than typing blind.

Opt-in, because it needs a desktop session and takes focus for a second:

    pytest -m live
"""

from __future__ import annotations

import sys
import time

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]

CASES = [
    ("ascii", "hello world"),
    ("cyrillic", "Привет, мир! Как дела?"),
    ("mixed", "Fortune VPN — релиз v2, 30 июля"),
    ("punctuation", "«ёлки» … — 42%"),
    ("surrogate pair", "готово 🎤"),
    ("newline", "первая строка\nвторая строка"),
    # Long enough to cross several SendInput batches, which is where a missing
    # inter-batch delay shows up as dropped characters.
    ("long", "раз два три четыре пять " * 8),
]


# Module-scoped: creating and destroying a Tcl interpreter per case is both
# slow and flaky (repeated teardown intermittently loses the Tcl library path
# and fails with "couldn't read file .../auto.tcl"). One window, cleared
# between cases.
@pytest.fixture(scope="module")
def text_widget():
    tkinter = pytest.importorskip("tkinter")

    root = tkinter.Tk()
    root.title("fortunevoice injection test")
    root.geometry("640x200+120+120")
    widget = tkinter.Text(root, font=("Segoe UI", 11))
    widget.pack(fill="both", expand=True)

    root.attributes("-topmost", True)
    root.update()
    root.lift()
    root.focus_force()
    widget.focus_set()
    root.update()

    try:
        yield root, widget
    finally:
        root.destroy()


def _own_the_foreground(root, timeout: float = 3.0) -> bool:
    """True once the foreground window belongs to this process.

    Compares process IDs rather than window handles: Tk's toplevel HWND is not
    what `winfo_id()` returns (that is the client area), and the PID check is
    both simpler and strictly stronger — it can only be true if the window in
    front is one of ours.
    """
    import os

    from fortunevoice import winapi

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.lift()
        root.focus_force()
        root.update()
        if winapi.window_process_id(winapi.foreground_window()) == os.getpid():
            time.sleep(0.1)  # let the focus change settle before typing
            root.update()
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("name,text", CASES, ids=[c[0] for c in CASES])
def test_types_into_a_focused_field(text_widget, name, text):
    from fortunevoice import injector

    root, widget = text_widget
    widget.delete("1.0", "end")
    root.update()

    if not _own_the_foreground(root):
        pytest.skip(
            "another window holds the foreground — refusing to type into it. "
            "Run this from a normal desktop session with nothing stealing focus."
        )

    assert injector.type_text(text), f"SendInput reported a failure for {name}"

    # Tk only processes the synthetic keystrokes when its event loop runs.
    deadline = time.monotonic() + 5
    typed = ""
    while time.monotonic() < deadline:
        root.update()
        typed = widget.get("1.0", "end-1c")
        if len(typed) >= len(text):
            break
        time.sleep(0.02)

    assert typed == text, f"{name}: typed {typed!r}, expected {text!r}"
