"""Reusable pieces of the look: rounded cards, switches, chips, nav rows.

Tk gives you square grey boxes. Everything with a curve in it is drawn on a
Canvas here, once, so the pages themselves stay readable.

The recurring trick is `Card`: a Canvas that paints a rounded rectangle and
hosts a normal Frame inset far enough that the Frame's own square corners
never poke through the drawn ones. Content inside is ordinary Tk, so labels
stay selectable and layout stays `pack`.
"""

from __future__ import annotations

from typing import Callable

from . import icons, theme


class Card:
    """A rounded panel. Add content to `.body`."""

    def __init__(self, parent, *, bg: str = theme.CARD, radius: int = 0,
                 outline: str = "", padx: int = 0, pady: int = 0,
                 parent_bg: str | None = None) -> None:
        import tkinter as tk

        self._bg = bg
        self._radius = theme.px(radius or 14)
        self._outline = outline
        self._parent_bg = parent_bg or parent["bg"]

        self.frame = tk.Frame(parent, bg=self._parent_bg)
        self.canvas = tk.Canvas(self.frame, bg=self._parent_bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        padx, pady = theme.px(padx or 18), theme.px(pady or 16)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(padx, pady, anchor="nw", window=self.body)
        self._padx, self._pady = padx, pady
        self.canvas.bind("<Configure>", self._redraw)
        self.body.bind("<Configure>", self._fit)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
        return self

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
        return self

    def _fit(self, _event=None) -> None:
        """Grow the canvas to whatever the content needs. Without this a Card
        collapses to zero height — a Canvas has no idea what is inside it."""
        height = self.body.winfo_reqheight() + self._pady * 2
        if self.canvas.winfo_height() != height:
            self.canvas.configure(height=height)

    def _redraw(self, _event=None) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        self.canvas.delete("card")
        theme.rounded_rect(self.canvas, 0, 0, width - 1, height - 1, self._radius,
                           fill=self._bg, outline=self._outline)
        for item in self.canvas.find_all():
            if item != self._window:
                self.canvas.addtag_withtag("card", item)
        self.canvas.tag_lower("card")
        self.canvas.itemconfigure(self._window, width=width - self._padx * 2)


class Switch:
    """An iOS-style toggle. Reads and writes through the callbacks it is given,
    so it can never drift from the value it displays."""

    W, H = theme.px(42), theme.px(24)

    def __init__(self, parent, get: Callable[[], bool], set_: Callable[[bool], None]) -> None:
        import tkinter as tk

        self._get, self._set = get, set_
        self.canvas = tk.Canvas(parent, width=self.W, height=self.H,
                                bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.bind("<Button-1>", self._toggle)
        self.paint()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def _toggle(self, _event=None) -> None:
        self._set(not self._get())
        self.paint()

    def paint(self) -> None:
        on = self._get()
        self.canvas.delete("all")
        theme.rounded_rect(self.canvas, 1, 1, self.W - 1, self.H - 1, (self.H - 2) // 2,
                           fill=theme.ACCENT if on else theme.LINE)
        r = (self.H - 8) / 2
        cx = self.W - 4 - r if on else 4 + r
        cy = self.H / 2
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#FFFFFF", outline="")


class Dropdown:
    """A value chip that opens a dark popup menu.

    tk's OptionMenu renders as a native grey button on Windows and cannot be
    talked out of it; a Menu, however, does honour bg/fg, so this is a label
    that posts one.
    """

    def __init__(self, parent, options: list[tuple[str, str]],
                 get: Callable[[], str], set_: Callable[[str], None]) -> None:
        import tkinter as tk

        self._options = options
        self._get, self._set = get, set_

        self.frame = tk.Frame(parent, bg=parent["bg"], cursor="hand2")
        self.canvas = tk.Canvas(self.frame, height=theme.px(30), bg=parent["bg"],
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="x")
        self._menu = tk.Menu(
            self.frame, tearoff=0, bg=theme.CARD_HI, fg=theme.TEXT,
            activebackground=theme.ACCENT, activeforeground="#FFFFFF",
            bd=0, relief="flat", font=theme.font(9),
        )
        for value, title in options:
            self._menu.add_command(label=title, command=lambda v=value: self._choose(v))
        for widget in (self.frame, self.canvas):
            widget.bind("<Button-1>", self._post)
        self.paint()

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
        return self

    def _choose(self, value: str) -> None:
        self._set(value)
        self.paint()

    def _post(self, event) -> None:
        self._menu.tk_popup(event.x_root, event.y_root)

    def _title(self) -> str:
        current = self._get()
        for value, title in self._options:
            if value == current:
                return title
        return current or "—"

    def paint(self) -> None:
        text = self._title()
        # Measured, not estimated. The old "7 px per character" guess was
        # calibrated at 100% scaling; on a 125% display the same string needs
        # ~9 px per character, so every chip came out too narrow and the
        # chevron sat on top of the last word.
        pad, gap = theme.px(12), theme.px(10)
        chevron_size = theme.px(12)
        width = pad + theme.text_width(text, theme.font(9)) + gap + chevron_size + pad
        height = theme.px(30)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        theme.rounded_rect(self.canvas, 0, theme.px(2), width - 1, height - theme.px(2),
                           theme.px(7), fill=theme.CARD_HI)
        self.canvas.create_text(pad, height / 2, text=text, anchor="w",
                                fill=theme.TEXT, font=theme.font(9))
        chevron = icons.photo(icons.image("chevron", chevron_size, theme.TEXT_MUTED,
                                          stroke=0.10))
        self.canvas._images = [chevron]
        self.canvas.create_image(width - pad - chevron_size / 2, height / 2,
                                 image=chevron)


class Chip:
    """A small tinted pill — the app badge on a history row, the shortcut
    display in Settings."""

    def __init__(self, parent, text: str, *, bg: str = theme.ACCENT_SOFT,
                 fg: str = theme.ACCENT_TEXT, size: int = 8) -> None:
        import tkinter as tk

        font = theme.font(size)
        width = theme.px(14) + theme.text_width(text, font)
        height = theme.px(19)
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=parent["bg"],
                                highlightthickness=0, bd=0)
        theme.rounded_rect(self.canvas, 0, 0, width - 1, height - 1, theme.px(6), fill=bg)
        self.canvas.create_text(width / 2, height / 2, text=text, fill=fg, font=font)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self


class NavItem:
    """One row in the sidebar. Active rows get the filled accent plate."""

    def __init__(self, parent, name: str, glyph: str, on_click: Callable[[str], None]) -> None:
        import tkinter as tk

        self.name = name
        self._glyph = glyph
        self._active = False
        self._images: list = []
        self.canvas = tk.Canvas(parent, height=theme.px(40), bg=parent["bg"],
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas._images = []
        self.canvas.bind("<Button-1>", lambda _e: on_click(name))
        self.canvas.bind("<Enter>", lambda _e: self.paint(hover=True))
        self.canvas.bind("<Leave>", lambda _e: self.paint(hover=False))
        self.canvas.bind("<Configure>", lambda _e: self.paint())

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def set_active(self, active: bool) -> None:
        self._active = active
        self.paint()

    def paint(self, hover: bool = False) -> None:
        width = self.canvas.winfo_width() or 220
        self.canvas.delete("all")
        if self._active:
            theme.rounded_rect(self.canvas, 0, theme.px(2), width - 1, theme.px(38),
                               theme.px(10), fill=theme.ACCENT)
            colour = "#FFFFFF"
        elif hover:
            theme.rounded_rect(self.canvas, 0, theme.px(2), width - 1, theme.px(38),
                               theme.px(10), fill=theme.CARD)
            colour = theme.TEXT
        else:
            colour = theme.TEXT_MUTED
        glyph = icons.photo(icons.image(self._glyph, theme.px(18), colour, stroke=0.085))
        self.canvas._images = [glyph]
        self.canvas.create_image(theme.px(20), theme.px(20), image=glyph)
        self.canvas.create_text(theme.px(44), theme.px(20), text=self.name,
                                anchor="w", fill=colour,
                                font=theme.font(11, "bold" if self._active else "normal"))


class IconButton:
    """A square rounded button holding one glyph — the actions above History."""

    def __init__(self, parent, glyph: str, command, size: int = 0, tooltip: str = "") -> None:
        import tkinter as tk

        self._glyph = glyph
        self._size = size = theme.px(size or 34)
        self.canvas = tk.Canvas(parent, width=size, height=size, bg=parent["bg"],
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas._images = []
        self.canvas.bind("<Button-1>", lambda _e: command())
        self.canvas.bind("<Enter>", lambda _e: self.paint(hover=True))
        self.canvas.bind("<Leave>", lambda _e: self.paint(hover=False))
        if tooltip:
            self.canvas.bind("<Enter>", lambda _e: (self.paint(hover=True),
                                                    self._show_tip(tooltip)), add="+")
            self.canvas.bind("<Leave>", lambda _e: self._hide_tip(), add="+")
        self._tip = None
        self.paint()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def paint(self, hover: bool = False) -> None:
        self.canvas.delete("all")
        theme.rounded_rect(self.canvas, 0, 0, self._size - 1, self._size - 1,
                           theme.px(9),
                           fill=theme.CARD_HI if hover else theme.CARD)
        glyph = icons.photo(icons.image(self._glyph, theme.px(16),
                                        theme.TEXT if hover else theme.TEXT_MUTED))
        self.canvas._images = [glyph]
        self.canvas.create_image(self._size / 2, self._size / 2, image=glyph)

    def _show_tip(self, text: str) -> None:
        import tkinter as tk

        if self._tip is not None:
            return
        tip = tk.Toplevel(self.canvas)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, bg=theme.CARD_HI, fg=theme.TEXT,
                 font=theme.font(8), padx=8, pady=4).pack()
        x = self.canvas.winfo_rootx()
        y = self.canvas.winfo_rooty() + self._size + 6
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class SettingRow:
    """Icon tile · title (+ subtitle) · control, the Settings list unit."""

    def __init__(self, parent, glyph: str, tint: str, title: str,
                 subtitle: str = "", last: bool = False) -> None:
        import tkinter as tk

        self.frame = tk.Frame(parent, bg=theme.CARD)
        self.frame._images = []
        self.frame.pack(fill="x")

        row = tk.Frame(self.frame, bg=theme.CARD)
        row.pack(fill="x", pady=theme.px(9))

        tile = icons.photo(icons.tile(glyph, theme.px(28), "#FFFFFF", tint))
        self.frame._images.append(tile)
        tk.Label(row, image=tile, bg=theme.CARD).pack(side="left",
                                                      padx=(0, theme.px(12)))

        text = tk.Frame(row, bg=theme.CARD)
        text.pack(side="left", fill="x", expand=True)
        theme.label(text, title, size=10).pack(anchor="w")
        if subtitle:
            theme.label(text, subtitle, size=8, colour=theme.TEXT_FAINT).pack(anchor="w")

        self.control = tk.Frame(row, bg=theme.CARD)
        self.control.pack(side="right")

        if not last:
            # Inset separator, aligned with the text rather than the icon —
            # a full-width rule makes the list look like a table.
            tk.Frame(self.frame, bg=theme.LINE, height=1).pack(
                fill="x", padx=(theme.px(40), 0))


def section_title(parent, text: str):
    """Small-caps group heading above a settings card."""
    return theme.label(parent, text.upper(), size=8, colour=theme.TEXT_FAINT,
                       weight="bold")


class ShortcutRecorder:
    """Click, then press the combination you want.

    Typing a shortcut as text is the wrong interaction for the one setting
    where a typo is invisible: a bad string parses into nothing, the hook
    never fires, and the app looks dead rather than misconfigured. Capturing
    the real keypress means what you pressed is what gets stored.

    Modifier state is read from the physical keyboard at the moment the
    trigger key arrives rather than from Tk's `event.state`, whose Alt bit
    differs between Tk builds and window managers.
    """

    # Tk keysym → the name the hotkey parser knows. Only the ones that differ;
    # letters, digits and F-keys fall through lowercased.
    _KEYSYM = {
        "space": "space", "Return": "enter", "Tab": "tab", "BackSpace": "backspace",
        "Insert": "insert", "Delete": "delete", "Home": "home", "End": "end",
        "Prior": "pageup", "Next": "pagedown", "Left": "left", "Up": "up",
        "Right": "right", "Down": "down", "grave": "`", "minus": "-",
        "equal": "=", "semicolon": ";", "apostrophe": "'", "comma": ",",
        "period": ".", "slash": "/", "backslash": "\\",
        "bracketleft": "[", "bracketright": "]",
    }
    _IGNORED = {
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Super_L", "Super_R", "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift",
    }

    def __init__(self, parent, get: Callable[[], str], set_: Callable[[str], bool]) -> None:
        import tkinter as tk

        self._get, self._set = get, set_
        self._listening = False
        self.canvas = tk.Canvas(parent, height=theme.px(30), width=theme.px(150),
                                bg=parent["bg"], highlightthickness=0, bd=0,
                                cursor="hand2", takefocus=1)
        self.canvas.bind("<Button-1>", self._begin)
        self.canvas.bind("<KeyPress>", self._captured)
        self.canvas.bind("<FocusOut>", lambda _e: self._end())
        self.paint()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def _begin(self, _event=None) -> None:
        self._listening = True
        self.canvas.focus_set()
        self.paint()

    def _end(self) -> None:
        self._listening = False
        self.paint()

    def _captured(self, event) -> str | None:
        if not self._listening:
            return None
        if event.keysym == "Escape":
            self._end()
            return "break"
        if event.keysym in self._IGNORED:
            return "break"  # a modifier alone is not a shortcut

        key = self._KEYSYM.get(event.keysym, event.keysym.lower())
        modifiers = []
        try:
            from .. import winapi

            if winapi.key_is_down(winapi.VK_CONTROL):
                modifiers.append("ctrl")
            if winapi.key_is_down(winapi.VK_MENU):
                modifiers.append("alt")
            if winapi.key_is_down(winapi.VK_SHIFT):
                modifiers.append("shift")
        except Exception:  # noqa: BLE001 - fall back to no modifiers
            pass

        self._set("+".join([*modifiers, key]))
        self._end()
        return "break"

    def paint(self) -> None:
        width = int(self.canvas["width"])
        self.canvas.delete("all")
        height = int(self.canvas["height"])
        if self._listening:
            theme.rounded_rect(self.canvas, 0, theme.px(2), width - 1,
                               height - theme.px(2), theme.px(7), fill=theme.ACCENT)
            self.canvas.create_text(width / 2, height / 2, text="Press keys…",
                                    fill="#FFFFFF", font=theme.font(9, "bold"))
            return
        theme.rounded_rect(self.canvas, 0, theme.px(2), width - 1,
                           height - theme.px(2), theme.px(7), fill=theme.CARD_HI)
        self.canvas.create_text(width / 2, height / 2, text=self._get() or "—",
                                fill=theme.TEXT, font=theme.font(9))
