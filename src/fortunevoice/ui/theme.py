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


def _display_scale() -> float:
    """Read the display scale once, at import.

    Safe to do here because `__main__` calls `set_dpi_awareness()` before any
    of this package is imported, so the number is already the real one. Doing
    it at import rather than per-window means module-level layout constants
    can be written in scaled pixels directly.
    """
    try:
        from .. import winapi

        return winapi.scale_factor()
    except Exception:  # noqa: BLE001 - tests and non-Windows imports
        return 1.0


SCALE = _display_scale()


def px(value: float) -> int:
    """Layout pixels, scaled for this display.

    Tk sizes widgets in raw pixels, and a DPI-aware process gets *physical*
    pixels — so a 40 px row really is 40 dots, which is right at 100% and half
    the intended size at 200%. Fonts are handled separately, by Tk's own
    `tk scaling`.
    """
    return int(round(value * SCALE))


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


def text_width(text: str, font_spec: tuple) -> int:
    """Width of `text` in pixels, measured by Tk rather than estimated.

    Guessing "N pixels per character" is how the dropdown chips came out too
    narrow on a 125% display: the estimate was calibrated at 100% scaling and
    Tk had since been told to draw fonts 25% larger, so the chevron ended up
    on top of the last word. `font.measure` accounts for the scaling, the
    typeface and the actual characters.

    Falls back to a rough estimate when no Tk interpreter exists yet (import
    time, or a headless test) — a wrong width is survivable, an exception at
    module import is not.
    """
    try:
        import tkinter.font as tkfont

        return int(tkfont.Font(font=font_spec).measure(text))
    except Exception:  # noqa: BLE001 - no interpreter, no display
        return px(len(text) * 7)


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
