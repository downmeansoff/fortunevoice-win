"""One palette and a few primitives, so every window looks like the same app.

Matches the macOS build's look: a deep navy surface, floating rounded cards a
step lighter than it, and a single blue accent that marks the one thing on a
screen worth looking at first.

Dark only, and not theme-aware. This UI appears *over* whatever the user is
working in, for a second at a time — a surface that tracked the system theme
would flash white on a light desktop at the exact moment they are looking
somewhere else.
"""

from __future__ import annotations

# Surfaces, darkest to lightest.
INK = "#0E1420"          # window
SIDEBAR = "#131B2A"      # nav rail
CARD = "#182234"         # content cards
CARD_HI = "#1E2941"      # hover / raised rows
LINE = "#25314A"         # hairline borders

TEXT = "#EAF0FA"
TEXT_MUTED = "#8B96AA"
TEXT_FAINT = "#5D6880"

ACCENT = "#2F7DF6"       # the one blue
ACCENT_DIM = "#245FBF"
ACCENT_SOFT = "#1D2B45"  # tinted chip backgrounds
ACCENT_TEXT = "#6BA6FF"  # text on a tinted chip

OK = "#34C759"
RECORDING = "#FF453A"
PROCESSING = "#FF9F0A"
ERROR = "#FF453A"

# Segoe UI is on every Windows 10/11; the fallbacks are for stripped images.
FONT = "Segoe UI"
FONT_MONO = "Consolas"


def font(size: int = 10, weight: str = "normal") -> tuple:
    return (FONT, size, weight)


def mono(size: int = 9) -> tuple:
    return (FONT_MONO, size, "normal")


def label(parent, text: str = "", size: int = 10, colour: str = TEXT,
          weight: str = "normal", bg: str | None = None):
    import tkinter as tk

    return tk.Label(
        parent, text=text, font=font(size, weight),
        bg=bg or parent["bg"], fg=colour, anchor="w", justify="left",
    )


def button(parent, text: str, command, primary: bool = False):
    """A flat button. ttk's themed buttons refuse a dark background on Windows
    — the native renderer wins — so these are plain tk widgets."""
    import tkinter as tk

    widget = tk.Button(
        parent, text=text, command=command,
        font=font(9, "bold" if primary else "normal"),
        bg=ACCENT if primary else CARD_HI,
        fg="#FFFFFF" if primary else TEXT,
        activebackground=ACCENT_DIM if primary else LINE,
        activeforeground="#FFFFFF" if primary else TEXT,
        relief="flat", bd=0, padx=16, pady=8, cursor="hand2", highlightthickness=0,
    )
    return widget


def entry(parent, textvariable=None, bg: str | None = None):
    import tkinter as tk

    return tk.Entry(
        parent, textvariable=textvariable, font=font(10),
        bg=bg or CARD, fg=TEXT, insertbackground=TEXT,
        relief="flat", bd=0, highlightthickness=0,
    )


def scrollbar(parent, command):
    """A thin dark scrollbar. tk's default is a native grey slab that ruins
    whatever surface it sits on."""
    import tkinter as tk

    return tk.Scrollbar(
        parent, command=command, bg=INK, troughcolor=INK,
        activebackground=LINE, highlightthickness=0, bd=0, width=10, relief="flat",
    )


def rounded_rect(canvas, x0, y0, x1, y1, r, fill="", outline="", width=1):
    """A rounded rectangle on a canvas.

    Tk's canvas has no primitive for one, and the usual
    polygon-with-smooth=True trick rounds the straight edges too. Built from
    two rectangles and four arcs/ovals instead, which stays crisp.
    """
    if fill:
        canvas.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline=fill)
        canvas.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline=fill)
        for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            canvas.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=fill, outline=fill)
    if outline:
        canvas.create_arc(x0, y0, x0 + 2 * r, y0 + 2 * r, start=90, extent=90,
                          style="arc", outline=outline, width=width)
        canvas.create_arc(x1 - 2 * r, y0, x1, y0 + 2 * r, start=0, extent=90,
                          style="arc", outline=outline, width=width)
        canvas.create_arc(x0, y1 - 2 * r, x0 + 2 * r, y1, start=180, extent=90,
                          style="arc", outline=outline, width=width)
        canvas.create_arc(x1 - 2 * r, y1 - 2 * r, x1, y1, start=270, extent=90,
                          style="arc", outline=outline, width=width)
        canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline, width=width)
        canvas.create_line(x0 + r, y1, x1 - r, y1, fill=outline, width=width)
        canvas.create_line(x0, y0 + r, x0, y1 - r, fill=outline, width=width)
        canvas.create_line(x1, y0 + r, x1, y1 - r, fill=outline, width=width)
