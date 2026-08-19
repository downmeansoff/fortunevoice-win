"""One palette and a few primitives, so every window looks like the same app.

Dressed like Claude Desktop: warm near-black surfaces rather than the usual
blue-grey, a single clay accent, and a serif for the headings. Three details
carry most of that look, and all three are cheap to get wrong —

* **The greys are warm.** Claude's dark surfaces sit around hue 40°, not 220°.
  A neutral #202020 next to them reads as a different app; a navy reads as a
  different decade.
* **Colour is rationed.** One clay accent, and muted earth tones where the eye
  needs grouping. Saturated system colours (Apple's blue, green, pink) are
  what made this look like macOS Settings.
* **Headings are serif.** Georgia, because it is on every Windows install and
  is the closest thing to the serif Claude sets its display type in.

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


# Surfaces, darkest to lightest. Warm greys — the whole palette sits near
# hue 40°, which is what separates "Claude dark" from "generic dark".
INK = "#1F1E1D"          # window
SIDEBAR = "#191817"      # nav rail, a step deeper than the page
CARD = "#262624"         # content cards
CARD_HI = "#30302E"      # hover / raised rows
LINE = "#454340"         # hairline borders — visible, still a hairline

TEXT = "#F5F4EE"         # warm off-white, never pure #FFF
TEXT_MUTED = "#B6B1A6"   # row subtitles: 7.2:1 on a card
# Section headers and captions. Was #7C766C — 3.37:1, which is the floor for
# LARGE text and these are 9 px. Small dim type is what made the window read
# as unfinished before anything else did.
TEXT_FAINT = "#918B80"

ACCENT = "#D97757"       # clay — the one accent
ACCENT_DIM = "#C15F3C"   # pressed / hover
ACCENT_SOFT = "#33291F"  # tinted chip backgrounds
ACCENT_TEXT = "#E4A183"  # text on a tinted chip

OK = "#7D9A72"           # muted sage, not a system green
RECORDING = "#D9534F"
PROCESSING = "#D4A27F"   # kraft
ERROR = "#D9534F"

def _first_installed(*candidates: str) -> str:
    """The first of these the machine actually has.

    Asked once, at import, through Tk itself rather than guessed from the
    Windows version: a font can be missing from an N edition or a stripped
    image, and falling back silently beats a window rendered in Courier.
    """
    try:
        import tkinter as tk
        import tkinter.font as tkfont

        root = tk.Tk()
        root.withdraw()
        try:
            installed = {name.lower() for name in tkfont.families()}
        finally:
            root.destroy()
    except Exception:  # noqa: BLE001 - no display yet, or no Tk at all
        return candidates[-1]
    for name in candidates:
        if name.lower() in installed:
            return name
    return candidates[-1]


# Windows 11's own UI face, in its three optical sizes. This is the single
# biggest difference between "a Tk app" and "a Windows app": Segoe UI Variable
# is drawn differently at each size — Small tightens the spacing and thickens
# the strokes so 9 px stays legible, Display opens it up so a heading does not
# look like a grown-up caption. Plain Segoe UI is one drawing stretched to
# every size, which is exactly what made the small type look weak.
FONT_SMALL = _first_installed("Segoe UI Variable Small", "Segoe UI", "Arial")
FONT = _first_installed("Segoe UI Variable Text", "Segoe UI", "Arial")
FONT_LARGE = _first_installed("Segoe UI Variable Display", "Segoe UI", "Arial")
FONT_MONO = _first_installed("Cascadia Mono", "Consolas", "Courier New")
# Display face for headings and figures. Constantia over Georgia: it was cut
# for screens, its Cyrillic is drawn rather than adapted, and Georgia's wide
# slab serifs read as a newspaper next to this palette.
FONT_SERIF = _first_installed("Constantia", "Georgia", "Times New Roman")

# Below this a caption needs the Small optical size; above it, Display.
_SMALL_UPTO = 9
_LARGE_FROM = 16


def font(size: int = 10, weight: str = "normal") -> tuple:
    """Body text, in the optical size the point size calls for."""
    if size <= _SMALL_UPTO:
        face = FONT_SMALL
    elif size >= _LARGE_FROM:
        face = FONT_LARGE
    else:
        face = FONT
    return (face, size, weight)


def mono(size: int = 9) -> tuple:
    return (FONT_MONO, size, "normal")


def serif(size: int = 20, weight: str = "normal") -> tuple:
    """Headings and big numbers. Everything else stays in the sans."""
    return (FONT_SERIF, size, weight)


def label(parent, text: str = "", size: int = 10, colour: str = TEXT,
          weight: str = "normal", bg: str | None = None, display: bool = False):
    import tkinter as tk

    return tk.Label(
        parent, text=text, font=(serif if display else font)(size, weight),
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


class scrollbar:  # noqa: N801 - stands in for tk.Scrollbar, keeps its name
    """A thin scrollbar drawn on a canvas.

    `tk.Scrollbar` on Windows renders through the native theme and ignores
    `bg` and `troughcolor` outright: the thumb came out #F0F0F0 whatever it was
    told, a bright light slab down the edge of a dark window. `ttk` is no help
    either — its Windows themes are equally unstyleable, and the one theme that
    would take colours (`clam`) can only be selected for the whole interpreter.

    So: a rounded thumb on the page's own background. Speaks the two halves of
    Tk's scrollbar protocol that a scrolling canvas needs — `set(first, last)`
    from the canvas, `command("moveto", …)` back to it.
    """

    WIDTH = 10

    def __init__(self, parent, command) -> None:
        import tkinter as tk

        self._command = command
        self._first, self._last = 0.0, 1.0
        self._drag_from: tuple[float, float] | None = None
        self.canvas = tk.Canvas(parent, width=px(self.WIDTH), bg=parent["bg"],
                                highlightthickness=0, bd=0)
        self.canvas.bind("<Configure>", lambda _e: self._paint())
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: self._release())
        self.canvas.bind("<Enter>", lambda _e: self._paint(hover=True))
        self.canvas.bind("<Leave>", lambda _e: self._paint(hover=False))
        self._hover = False

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def set(self, first, last) -> None:
        self._first, self._last = float(first), float(last)
        self._paint()

    # ── internals ────────────────────────────────────────────────────────

    def _thumb(self) -> tuple[int, int] | None:
        """Pixel span of the thumb, or None when everything already fits."""
        height = self.canvas.winfo_height()
        if height < 8 or self._last - self._first >= 0.999:
            return None
        top = int(self._first * height)
        bottom = max(top + px(24), int(self._last * height))
        return top, min(bottom, height)

    def _paint(self, hover: bool | None = None) -> None:
        if hover is not None:
            self._hover = hover
        self.canvas.delete("all")
        span = self._thumb()
        if span is None:
            return
        top, bottom = span
        inset = px(2)
        width = self.canvas.winfo_width()
        rounded_rect(self.canvas, inset, top, width - inset - 1, bottom - 1,
                     (width - 2 * inset) // 2,
                     fill=TEXT_FAINT if self._hover else LINE)

    def _press(self, event) -> None:
        span = self._thumb()
        if span is None:
            return
        top, bottom = span
        if top <= event.y <= bottom:
            self._drag_from = (event.y, self._first)
        else:
            # Clicked the track: centre the thumb on the click, which is what
            # every scrollbar built this decade does.
            height = max(1, self.canvas.winfo_height())
            page = self._last - self._first
            self._command("moveto", _clamp(event.y / height - page / 2))

    def _drag(self, event) -> None:
        if self._drag_from is None:
            return
        start_y, start_first = self._drag_from
        height = max(1, self.canvas.winfo_height())
        self._command("moveto", _clamp(start_first + (event.y - start_y) / height))

    def _release(self) -> None:
        self._drag_from = None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


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
