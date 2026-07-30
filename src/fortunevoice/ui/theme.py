"""One palette and a few primitives, so every window looks like the same app.

Dark by default and not theme-aware: this UI appears *over* whatever the user
is working in, for a second at a time. A surface that tracks the system theme
would flash white on a light desktop at the exact moment the user is looking
somewhere else, and the macOS original was a solid black capsule for the same
reason.
"""

from __future__ import annotations

INK = "#0D0E12"          # window surface
INK_RAISED = "#16181F"   # cards, input backgrounds
LINE = "#262A35"         # hairline borders
TEXT = "#E8EAF0"
TEXT_MUTED = "#8A90A0"
TEXT_FAINT = "#5C6272"

ACCENT = "#6C8CFF"       # interactive / focus
RECORDING = "#FF4D5E"
PROCESSING = "#FFB020"
OK = "#4ADE80"
ERROR = "#FF4D5E"

# Segoe UI is on every Windows 10/11; the fallbacks are for stripped images.
FONT = "Segoe UI"
FONT_MONO = "Consolas"


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT, size, weight)


def mono(size: int = 9) -> tuple:
    return (FONT_MONO, size, "normal")


def style_window(window) -> None:
    """Common chrome for a normal (focusable) window."""
    window.configure(bg=INK)


def button(parent, text: str, command, primary: bool = False, width: int = 0):
    """A flat button. ttk's themed buttons refuse a dark background on Windows
    (the native renderer wins), so these are plain tk widgets."""
    import tkinter as tk

    widget = tk.Button(
        parent,
        text=text,
        command=command,
        font=font(9, "bold" if primary else "normal"),
        bg=ACCENT if primary else INK_RAISED,
        fg="#0B0D12" if primary else TEXT,
        activebackground=ACCENT if primary else LINE,
        activeforeground="#0B0D12" if primary else TEXT,
        relief="flat",
        bd=0,
        padx=14,
        pady=7,
        cursor="hand2",
        highlightthickness=0,
    )
    if width:
        widget.configure(width=width)
    return widget


def label(parent, text: str = "", size: int = 10, colour: str = TEXT, weight: str = "normal"):
    import tkinter as tk

    return tk.Label(
        parent, text=text, font=font(size, weight), bg=parent["bg"], fg=colour,
        anchor="w", justify="left",
    )


def entry(parent, textvariable=None):
    import tkinter as tk

    return tk.Entry(
        parent,
        textvariable=textvariable,
        font=font(10),
        bg=INK_RAISED,
        fg=TEXT,
        insertbackground=TEXT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=ACCENT,
    )


def scrollbar(parent, command):
    """A thin dark scrollbar. tk's default is a native grey slab that ruins the
    surface it sits on."""
    import tkinter as tk

    return tk.Scrollbar(
        parent,
        command=command,
        bg=INK,
        troughcolor=INK,
        activebackground=LINE,
        highlightthickness=0,
        bd=0,
        width=10,
        relief="flat",
    )
