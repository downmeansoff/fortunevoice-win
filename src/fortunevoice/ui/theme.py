"""One palette and a few primitives, so every window looks like the same app.

**Paper.** A warm off-white page, ink-dark text, hairlines where a boundary
genuinely exists, and one accent — an iron-gall blue that appears on the
active tab, the primary button, the bars and the meters, and nowhere else.

Three decisions carry it, and all three are subtractions:

* **There are no cards.** Content sits directly on the page, separated by
  space and a hairline. The window used to be a dark app wearing card-shaped
  jewellery — a rounded rectangle inside a rounded rectangle, each with its
  own fill — which is what made it read as a dashboard template. `CARD` is now
  the page colour, so a card is simply padding.
* **Colour is rationed to one.** Not six tinted icon tiles down the left of
  the settings rows. Grouping is done by a heading and a rule, which is what
  headings and rules are for.
* **The serif earns its place exactly three times**: the page title, the big
  figures, and the transcript itself. Setting a person's own words in a text
  serif is the whole argument of this design — the transcript is the thing the
  user made, and everything else on the page is administration.

Light, and deliberately not theme-aware. The pill and the result panel appear
*over* whatever the user is working in, so they carry their own border and
their own ground rather than tracking a system theme that may not match the
window underneath.
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


# ── surfaces ─────────────────────────────────────────────────────────────
# One page colour, and two quieter grounds for the two things that genuinely
# sit *in* the page rather than on it: a hover band and a sunken well.
PAPER = "#F7F4EE"        # the window, the masthead, every page, every row
PAPER_2 = "#F1EDE4"      # row hover; secondary button hover
WELL = "#EDE8DE"         # sunken: the dictionary editor, meter tracks
RAISED = "#FCFAF6"       # popup menus and tooltips — the only lighter surface

# ── rules and strokes ────────────────────────────────────────────────────
# Two weights of hairline, because a structural rule and a list separator are
# not the same thing, and one STROKE for anything the user must be able to
# find the edge of. A prettier #C7BEAC measures 1.66:1 against the page and
# would make every control boundary a suggestion.
RULE = "#CFC5B2"         # masthead, section headings, chart baseline
RULE_SOFT = "#E6E0D4"    # between rows in a list
STROKE = "#766E60"       # control boundaries — 4.65:1, never lighter

# ── text, four tiers and no fifth ────────────────────────────────────────
TEXT = "#1B1917"         # 15.8:1 — titles, row titles, transcripts, values
TEXT_MID = "#4A453D"     # 8.8:1  — numbers in lists, secondary body
TEXT_MUTED = "#6E675C"   # 5.2:1  — captions, row hints, inactive nav
# The floor. Nothing lighter carries a word: the tempting #8C8477 measures
# 3.36:1, which is the same mistake this file already made once in the dark
# palette with #7C766C.
TEXT_FAINT = "#756D5F"   # 4.7:1  — timestamps, footnotes, placeholders

# ── the one accent, and it is ink rather than paint ──────────────────────
ACCENT = "#26456B"       # 9.0:1 on paper; white on it is 9.9:1
ACCENT_DIM = "#172B45"   # pressed
ACCENT_SOFT = "#E7E9EF"  # menu highlight, text selection
ACCENT_TEXT = "#26456B"  # accent-coloured text sits on the page, not a chip

# ── state, never used for grouping ───────────────────────────────────────
OK = "#3F6B4A"           # copied, saved, Ollama reachable
RECORDING = "#A4322A"
PROCESSING = "#8A6216"   # amber, warm enough to read as "working"
ERROR = "#A4322A"

# ── the floating surfaces, which must read over any desktop ──────────────
# The pill and the result panel appear over the user's own window, so they
# cannot borrow contrast from a background they do not own. An outer border
# against a white desktop, a light fill against a dark one, and a printed
# bevel along the top edge instead of the drop shadow Tk cannot draw.
PILL_EDGE = "#4A453D"
PILL_LIFT = "#FFFFFF"

# Old names, pointed at their new roles, so every existing call site keeps
# working while the layout is rewritten page by page. INK is the page — a
# `widgets.Card` on it is now invisible padding, which is exactly right.
INK = PAPER
SIDEBAR = PAPER
CARD = PAPER
CARD_HI = PAPER_2
LINE = RULE

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
        bg=ACCENT if primary else PAPER_2,
        # Not white. A secondary button on paper is ink on a faint band; white
        # here was invisible the moment the ground stopped being near-black.
        fg="#FFFFFF" if primary else TEXT,
        activebackground=ACCENT_DIM if primary else WELL,
        activeforeground="#FFFFFF" if primary else TEXT,
        relief="flat", bd=0, padx=16, pady=8, cursor="hand2", highlightthickness=0,
    )
    if not primary:
        # A findable edge, since the fill is only 1.13:1 against the page.
        widget.configure(highlightthickness=1, highlightbackground=STROKE,
                         highlightcolor=STROKE)
    return widget


def entry(parent, textvariable=None, bg: str | None = None):
    import tkinter as tk

    return tk.Entry(
        parent, textvariable=textvariable, font=font(10),
        bg=bg or PAPER, fg=TEXT, insertbackground=TEXT,
        selectbackground=ACCENT_SOFT, selectforeground=TEXT,
        relief="flat", bd=0, highlightthickness=0,
    )


def rule(parent, colour: str = RULE, pady=0):
    """A hairline. One physical pixel at 100%, two at 150%, which is right."""
    import tkinter as tk

    line = tk.Frame(parent, bg=colour, height=max(1, px(1)))
    line.pack(fill="x", pady=pady)
    return line


def text_button(parent, text: str, command, danger: bool = False):
    """A word that acts, for the actions that live beside a page title.

    Not a tile. A 34 px rounded square with a glyph in it is how "export" and
    "delete everything" came to look like the same control — and how the most
    destructive action in the app ended up as an unlabelled icon next to the
    window's own close button.
    """
    import tkinter as tk

    widget = tk.Label(parent, text=text, font=font(9), bg=parent["bg"],
                      fg=TEXT_MUTED, cursor="hand2")
    hot = ERROR if danger else TEXT

    def enter(_event=None):
        widget.configure(fg=hot)

    def leave(_event=None):
        widget.configure(fg=TEXT_MUTED)

    widget.bind("<Enter>", enter)
    widget.bind("<Leave>", leave)
    widget.bind("<Button-1>", lambda _e: command())
    return widget


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
