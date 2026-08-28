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

from ..strings import t
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
        # Tagged so the window can re-read every control from config when
        # it is shown: the tray changes the same settings behind its back.
        self.canvas._fv_paint = self.paint
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
                 get: Callable[[], str], set_: Callable[[str], None],
                 refresh: Callable[[], list[tuple[str, str]]] | None = None) -> None:
        import tkinter as tk

        self._options = options
        self._get, self._set = get, set_
        # Options that can change while the window is open. The Ollama model
        # list is built by asking Ollama, and Ollama on Windows shuts itself
        # down when idle — so a list built once at window-open time silently
        # collapsed to the single model already configured.
        self._refresh = refresh

        self.frame = tk.Frame(parent, bg=parent["bg"], cursor="hand2")
        self.canvas = tk.Canvas(self.frame, height=theme.px(30), bg=parent["bg"],
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack(fill="x")
        self._menu = tk.Menu(
            self.frame, tearoff=0, bg=theme.CARD_HI, fg=theme.TEXT,
            activebackground=theme.ACCENT, activeforeground="#FFFFFF",
            bd=0, relief="flat", font=theme.font(9),
        )
        self._fill(options)
        for widget in (self.frame, self.canvas):
            widget.bind("<Button-1>", self._post)
        # Tagged so the window can re-read every control from config when
        # it is shown: the tray changes the same settings behind its back.
        self.canvas._fv_paint = self.paint
        self.paint()

    def _fill(self, options: list[tuple[str, str]]) -> None:
        self._options = options
        self._menu.delete(0, "end")
        for value, title in options:
            self._menu.add_command(label=title, command=lambda v=value: self._choose(v))

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
        return self

    def _choose(self, value: str) -> None:
        self._set(value)
        self.paint()

    def _post(self, event) -> None:
        if self._refresh is not None:
            try:
                options = self._refresh()
            except Exception:  # noqa: BLE001 - a stale list beats no menu
                options = []
            if options:
                self._fill(options)
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
        width = pad + theme.text_width(text, theme.font(10)) + gap + chevron_size + pad
        height = theme.px(30)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        # The value, and a rule under it. A filled pill made every
        # setting look like a button you had to press rather than a
        # value you could read, and put a dozen grey blocks down the
        # right edge of a page whose whole argument is that nothing is
        # filled. The underline is what says "this one opens".
        self.canvas.create_text(pad, height / 2 - theme.px(1), text=text,
                                anchor="w", fill=theme.TEXT, font=theme.font(10))
        self.canvas.create_rectangle(pad, height - theme.px(5), width - pad,
                                     height - theme.px(5) + max(1, theme.px(1)),
                                     fill=theme.STROKE, outline="")
        chevron = icons.photo(icons.image("chevron", chevron_size, theme.TEXT_MUTED,
                                          stroke=0.10))
        self.canvas._images = [chevron]
        self.canvas.create_image(width - pad - chevron_size / 2, height / 2,
                                 image=chevron)


class Chip:
    """A small label with an edge.

    Two forms. Filled is the quiet one used inside a list; `outline=True` is a
    hairline box on the page itself, for the shortcut in the masthead — a
    filled pill there would be the second-loudest thing on a page whose whole
    argument is that nothing is filled.
    """

    def __init__(self, parent, text: str, *, bg: str = theme.WELL,
                 fg: str = theme.TEXT_MUTED, size: int = 8,
                 outline: bool = False, mono: bool = False) -> None:
        import tkinter as tk

        self._font = theme.mono(size + 1) if mono else theme.font(size)
        self._fg, self._bg, self._outline = fg, bg, outline
        self.canvas = tk.Canvas(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        """The masthead chip shows the live shortcut, so it has to be able to
        change when the user rebinds it."""
        width = theme.px(16) + theme.text_width(text, self._font)
        height = theme.px(20)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        if self._outline:
            self.canvas.create_rectangle(0, 0, width - 1, height - 1,
                                         outline=theme.STROKE,
                                         width=max(1, theme.px(1)))
        else:
            theme.rounded_rect(self.canvas, 0, 0, width - 1, height - 1,
                               theme.px(3), fill=self._bg)
        self.canvas.create_text(width / 2, height / 2, text=text,
                                fill=self._fg, font=self._font)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self


class NavItem:
    """One tab in the masthead. The active one is underlined, not filled."""

    def __init__(self, parent, name: str, glyph: str, on_click: Callable[[str], None],
                 label: str | None = None) -> None:
        import tkinter as tk

        self.name = name
        # The key the app switches on stays English; only the text shown is
        # translated, so a language change cannot break navigation.
        self._label = label or name
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
        self.canvas.delete("all")
        # Sized to its own text, and always to the BOLD width: the active tab
        # is bold, and a tab that changed width when you selected it would
        # shove its neighbours along the row.
        label = self._label
        width = theme.text_width(label, theme.font(10, "bold")) + theme.px(2)
        height = theme.px(34)
        self.canvas.configure(width=width, height=height)
        colour = theme.TEXT if (self._active or hover) else theme.TEXT_MUTED
        # No glyph. Four words across the top do not need pictures to tell
        # them apart, and the icons were four more shapes competing with the
        # page title directly beneath them.
        self.canvas._images = []
        self.canvas.create_text(width / 2, theme.px(13), text=label, fill=colour,
                                font=theme.font(10, "bold" if self._active else "normal"))
        if self._active:
            # Two pixels of accent, sitting on the masthead rule. This is the
            # only thing that says which tab you are on, and it is enough —
            # the filled plate it replaces was a button pretending to be a
            # location.
            self.canvas.create_rectangle(0, height - max(2, theme.px(2)),
                                         width, height, fill=theme.ACCENT, outline="")
        elif hover:
            self.canvas.create_rectangle(0, height - max(1, theme.px(1)),
                                         width, height, fill=theme.RULE, outline="")


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
    """Title (+ hint) · control. The Settings list unit.

    No icon tile. A dozen tinted rounded squares down the left edge were the
    loudest thing on the page and the least informative: nobody looks up a
    setting by its glyph, and the six tints grouped rows that a heading and a
    rule already group. Deleting the column also gives the hint its full
    measure, which is where the words that actually help live.
    """

    def __init__(self, parent, glyph: str, tint: str, title: str,
                 subtitle: str = "", last: bool = False) -> None:
        import tkinter as tk

        self.frame = tk.Frame(parent, bg=theme.PAPER)
        self.frame._images = []
        self.frame.pack(fill="x")

        row = tk.Frame(self.frame, bg=theme.PAPER)
        row.pack(fill="x", pady=theme.px(14))

        text = tk.Frame(row, bg=theme.PAPER)
        text.pack(side="left", fill="x", expand=True)
        theme.label(text, title, size=10).pack(anchor="w")
        # Kept on the row so callers can swap it — the shortcut row says
        # something different while it is listening for keys.
        self.subtitle = None
        if subtitle:
            self.subtitle = theme.label(text, subtitle, size=9,
                                        colour=theme.TEXT_MUTED)
            self.subtitle.pack(anchor="w", pady=(theme.px(4), 0))

        # Fixed width, so every control in the list shares one right edge
        # whatever it is: a switch, a chord, or "System default".
        self.control = tk.Frame(row, bg=theme.PAPER, width=theme.px(260),
                                height=theme.px(30))
        self.control.pack(side="right")
        self.control.pack_propagate(False)

        if not last:
            tk.Frame(self.frame, bg=theme.RULE_SOFT,
                     height=max(1, theme.px(1))).pack(fill="x")


def section_title(parent, text: str):
    """Group heading, with the rule that carries it across the page.

    Sentence case, not the shouty small-caps this started as: at 8 px, all-caps
    Cyrillic is a grey smear that has to be decoded rather than read.

    The rule is what replaces the card. A heading floating above a group of
    rows does not say where the group begins; a heading with a hairline
    running from its last letter to the right margin does, and costs nothing.
    """
    import tkinter as tk

    band = tk.Frame(parent, bg=parent["bg"])
    theme.label(band, text, size=11, colour=theme.TEXT, weight="bold",
                bg=parent["bg"]).pack(side="left")
    tk.Frame(band, bg=theme.RULE, height=max(1, theme.px(1))).pack(
        side="left", fill="x", expand=True, padx=(theme.px(14), 0))
    return band


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
    # Keys that are never a shortcut on their own. Tk on Windows reports the
    # Windows key as Win_L/Win_R, NOT the X11 name Super_L — listing only the
    # X11 spelling let a lone Win press through as the literal key "win_l",
    # which the parser then rejected with "unknown key".
    _IGNORED = {
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Win_L", "Win_R", "Super_L", "Super_R", "Meta_L", "Meta_R",
        "Caps_Lock", "Num_Lock", "Scroll_Lock", "ISO_Level3_Shift",
        "App", "Menu",
    }

    def __init__(self, parent, get: Callable[[], str], set_: Callable[[str], bool],
                 on_listening: Callable[[bool], None] | None = None,
                 capture: Callable | None = None) -> None:
        import tkinter as tk

        self._get, self._set = get, set_
        self._on_listening = on_listening
        # `capture(on_chord, on_cancel)` grabs the next chord with a global
        # keyboard hook and returns something with .stop(). Preferred over the
        # Tk bindings below, because the thing being recorded IS a global
        # hotkey: reading it through window focus made it depend on Windows
        # granting foreground to a background thread, which it does not owe us.
        # The Tk path stays as the fallback for tests and for a UI running
        # without an App.
        self._capture_factory = capture
        self._capture = None
        self._listening = False
        self._binding = None
        self.canvas = tk.Canvas(parent, height=theme.px(30), width=theme.px(150),
                                bg=parent["bg"], highlightthickness=0, bd=0,
                                cursor="hand2", takefocus=1)
        self.canvas.bind("<Button-1>", self._begin)
        self.paint()

    def bind_clickable(self, *widgets) -> None:
        """Let a click anywhere on the row start recording.

        A 150 px chip is a small target for the one setting people most want
        to change, and a click that lands a pixel outside it silently does
        nothing.
        """
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)

        # Every descendant, not just the direct children. A SettingRow keeps
        # its title and hint inside a frame, so clicking the words "Сочетание
        # клавиш" — the obvious place to click — landed on a label nobody had
        # bound and did nothing.
        for root in widgets:
            for widget in descendants(root):
                widget.bind("<Button-1>", self._begin, add="+")
                try:
                    widget.configure(cursor="hand2")
                except Exception:  # noqa: BLE001 - not every widget takes a cursor
                    pass

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def _begin(self, _event=None) -> str:
        if self._listening:
            return "break"
        self._listening = True
        self.canvas.focus_set()
        if self._capture_factory is not None:
            # The callbacks arrive on the hook thread; hop to the UI thread
            # before touching a single widget.
            from . import ui

            self._capture = self._capture_factory(
                lambda chord: ui.call(lambda: self._chord(chord)),
                lambda: ui.call(self._end),
            )
        else:
            # Fallback: bound on the TOPLEVEL, not on the chip, so a stray
            # click elsewhere in the window cannot send the keypress somewhere
            # that ignores it while this widget still looks like it is
            # listening. Not bind_all — the matching unbind_all wipes every
            # other <KeyPress> handler in the app, not just ours; keeping the
            # funcid removes exactly one.
            window = self.canvas.winfo_toplevel()
            self._binding = window.bind("<KeyPress>", self._captured, add="+")
        if self._on_listening:
            self._on_listening(True)
        self.paint()
        return "break"

    def cancel(self) -> None:
        """Stop listening, from outside. Closing the window while the chip was
        armed used to leave the global hook installed: it swallows keys, so the
        next keystroke anywhere vanished and the app's own hotkey stayed paused
        — the keyboard half-dead with nothing on screen to explain it."""
        if self._listening:
            self._end()

    def _end(self) -> None:
        self._listening = False
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._binding is not None:
            try:
                self.canvas.winfo_toplevel().unbind("<KeyPress>", self._binding)
            except Exception:  # noqa: BLE001 - window already gone
                pass
            self._binding = None
        if self._on_listening:
            self._on_listening(False)
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
            # Win counts as a modifier, so Win+D records as "win+d" rather
            # than being thrown away with the bare-Win presses.
            if (winapi.key_is_down(winapi.VK_LWIN)
                    or winapi.key_is_down(winapi.VK_RWIN)):
                modifiers.append("win")
        except Exception:  # noqa: BLE001 - fall back to no modifiers
            pass

        self._chord("+".join([*modifiers, key]))
        return "break"

    def _chord(self, chord: str) -> None:
        """One captured chord, from whichever route delivered it."""
        self._set(chord)
        self._end()

    def paint(self) -> None:
        # Sized to its text, like every other chip in the column. A fixed
        # 150 px made "ctrl+alt+space" fill the whole box while "Русский" sat
        # in a third of one, so the right-hand column read as a mistake.
        label = (t("settings.shortcut_press") if self._listening
                 else (self._get() or "—"))
        pad = theme.px(12)
        chord_font = theme.mono(9)
        width = max(theme.px(96),
                    pad + theme.text_width(label, chord_font) + pad)
        height = theme.px(30)
        self.canvas.configure(width=width, height=height)
        self.canvas.delete("all")
        if self._listening:
            # Filled, and the only filled control in the window. This is
            # a mode the user is IN — the keyboard is being swallowed
            # while it lasts — and a mode you cannot see is how the
            # whole keyboard ends up feeling broken.
            self.canvas.create_rectangle(0, theme.px(2), width - 1,
                                         height - theme.px(2),
                                         fill=theme.ACCENT, outline="")
            self.canvas.create_text(width / 2, height / 2,
                                    text=t("settings.shortcut_press"),
                                    fill="#FFFFFF", font=theme.font(9, "bold"))
            return
        # A chord is something you type, so it is set in the mono face,
        # in a hairline box — the same box the masthead shows it in.
        self.canvas.create_rectangle(0, theme.px(3), width - 1,
                                     height - theme.px(3),
                                     outline=theme.STROKE, width=max(1, theme.px(1)))
        self.canvas.create_text(width / 2, height / 2, text=self._get() or "—",
                                fill=theme.TEXT, font=chord_font)
