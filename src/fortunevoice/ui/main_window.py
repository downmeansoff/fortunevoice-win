"""The main window: History, Insights, Dictionary, Settings.

Laid out like the macOS build — a nav rail on the left, one page at a time on
the right, everything sitting in rounded cards on a deep navy surface.

Two structural notes:

* **Pages are built once and raised, not rebuilt.** Settings holds live
  widgets bound to config, and History can hold hundreds of rows; tearing them
  down on every tab switch would drop scroll position and flicker.
* **History rows are Labels in cards, not one big Text widget.** Cards are
  what make the page read like the macOS original, and the selection a Text
  would give is replaced by click-to-copy on the card itself.
"""

from __future__ import annotations

import datetime as dt

from .. import (
    config,
    dictionary,
    injector,
    metrics,
    paths,
    shortcut,
    stats,
    winapi,
)
from ..log import get as get_logger
from ..strings import t
from ..store import DictationStore
from . import icons, theme, widgets

logger = get_logger("ui.main")

WIDTH = theme.px(1000)
HEIGHT = theme.px(680)
SIDEBAR_W = theme.px(232)
# (internal key, glyph). The visible label comes from the catalogue at paint
# time, so the key can stay stable while the text is translated.
PAGES = [("History", "clock"), ("Insights", "chart"),
         ("Dictionary", "book"), ("Settings", "gear")]
_PAGE_LABEL = {"History": "nav.history", "Insights": "nav.insights",
               "Dictionary": "nav.dictionary", "Settings": "nav.settings"}

# Icon tint per settings row. Colour still carries the grouping when the eye
# skims, but from one muted earth family rather than the saturated system
# palette — a row of Apple-bright tiles was the single loudest thing on the
# page and read as macOS Settings, not as this app.
TINT_BLUE = "#CC785C"    # book cloth — the brand's own clay
TINT_INDIGO = "#B0705C"
TINT_TEAL = "#7D9A8E"    # sage
TINT_GREEN = "#7D9A72"
TINT_ORANGE = "#D4A27F"  # kraft
TINT_PINK = "#A8757F"
TINT_GREY = "#6E6A62"


class MainWindow:
    def __init__(self, store: DictationStore | None = None, app=None) -> None:
        self._store = store or DictationStore()
        self._app = app
        self._window = None
        self._page = "History"
        self._nav: dict[str, widgets.NavItem] = {}
        self._pages: dict[str, object] = {}
        self._images: list = []
        self._search_var = None
        self._search_is_placeholder = lambda: False
        self._history_body = None
        self._insights_body = None
        self._dictionary_text = None

    def show(self, app=None) -> None:
        from . import ui

        if app is not None:
            self._app = app
        ui.call(self._show)

    # ── UI thread only ───────────────────────────────────────────────────

    def _show(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._build()
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()
        self._select(self._page)

    def _build(self) -> None:
        import tkinter as tk

        from .. import assets
        from . import ui

        window = tk.Toplevel(ui.root)
        window.title("FortuneVoice")
        window.geometry(_remembered_geometry())
        window.minsize(theme.px(880), theme.px(560))
        window.configure(bg=theme.INK)
        try:
            window.iconbitmap(str(assets.icon_path()))
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        # Closing hides: the app lives in the tray, and destroying the window
        # would throw away the loaded history for no reason.
        window.protocol("WM_DELETE_WINDOW", self._on_close)
        # Tk styles the client area only, so a dark app otherwise gets a white
        # Windows title bar sitting on top of it.
        window.update_idletasks()
        winapi.use_dark_titlebar(window)
        self._window = window

        self._build_sidebar(window)

        content = tk.Frame(window, bg=theme.INK)
        content.pack(side="left", fill="both", expand=True, padx=(6, 26), pady=26)
        self._content = content

        for name, _glyph in PAGES:
            page = tk.Frame(content, bg=theme.INK)
            self._pages[name] = page
            builder = getattr(self, f"_build_{name.lower()}")
            builder(page)

    def _on_close(self) -> None:
        """Remember where the window was before hiding it.

        Saved on close rather than on every <Configure>: dragging a window
        fires that event dozens of times a second, and each one would rewrite
        config.json.
        """
        try:
            if self._window.state() == "normal":
                config.set("FVWindowGeometry", self._window.winfo_geometry())
        except Exception:  # noqa: BLE001 - never block closing over this
            logger.debug("could not save window geometry", exc_info=True)
        self._window.withdraw()

    # ── sidebar ──────────────────────────────────────────────────────────

    def _build_sidebar(self, parent) -> None:
        import tkinter as tk

        rail = widgets.Card(parent, bg=theme.SIDEBAR, radius=16, padx=14, pady=18)
        rail.frame.pack(side="left", fill="y", padx=(18, 0), pady=18)
        rail.canvas.configure(width=SIDEBAR_W)

        identity = tk.Frame(rail.body, bg=theme.SIDEBAR)
        identity.pack(fill="x", pady=(2, 22))
        mark = icons.photo(icons.tile("mic", 40, "#FFFFFF", theme.ACCENT, radius=0.30))
        self._images.append(mark)
        tk.Label(identity, image=mark, bg=theme.SIDEBAR).pack(side="left", padx=(2, 12))
        text = tk.Frame(identity, bg=theme.SIDEBAR)
        text.pack(side="left")
        # The product name is set in the display face, like the page titles —
        # it is the wordmark, not a label.
        theme.label(text, t("app.name"), size=13, display=True,
                    bg=theme.SIDEBAR).pack(anchor="w")
        theme.label(text, t("app.tagline"), size=8, colour=theme.TEXT_MUTED,
                    bg=theme.SIDEBAR).pack(anchor="w")

        for name, glyph in PAGES:
            item = widgets.NavItem(rail.body, name, glyph, self._select,
                                   label=t(_PAGE_LABEL[name]))
            item.pack(fill="x", pady=2)
            self._nav[name] = item

        spacer = tk.Frame(rail.body, bg=theme.SIDEBAR, height=1)
        spacer.pack(fill="both", expand=True, pady=10)
        theme.label(rail.body, t("app.runs_locally"), size=8,
                    colour=theme.TEXT_FAINT, bg=theme.SIDEBAR).pack(anchor="w", pady=(0, 2))

    def _select(self, name: str) -> None:
        self._page = name
        for page_name, item in self._nav.items():
            item.set_active(page_name == name)
        for page_name, page in self._pages.items():
            if page_name == name:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        refresh = getattr(self, f"_refresh_{name.lower()}", None)
        if refresh:
            refresh()

    # ── shared page furniture ────────────────────────────────────────────

    def _header(self, parent, title: str):
        import tkinter as tk

        bar = tk.Frame(parent, bg=theme.INK)
        bar.pack(fill="x", pady=(0, 18))
        # Serif, and not bold: the display face carries the weight on its own,
        # and Georgia Bold at 22 px is a heavier thing than this page needs.
        theme.label(bar, title, size=22, display=True).pack(side="left")
        return bar

    def _scroll_area(self, parent):
        """A vertically scrolling region that still stretches its content to
        the full width — the default canvas window does not."""
        import tkinter as tk

        holder = tk.Frame(parent, bg=theme.INK)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=theme.INK, highlightthickness=0, bd=0)
        bar = theme.scrollbar(holder, canvas.yview)
        inner = tk.Frame(canvas, bg=theme.INK)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_inner(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas(event) -> None:
            canvas.itemconfigure(window, width=event.width)

        inner.bind("<Configure>", on_inner)
        canvas.bind("<Configure>", on_canvas)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        def on_wheel(event) -> None:
            canvas.yview_scroll(-event.delta // 120, "units")

        for widget in (canvas, inner):
            widget.bind("<MouseWheel>", on_wheel)
        inner.bind(
            "<Enter>",
            lambda _e: canvas.bind_all("<MouseWheel>", on_wheel), add="+",
        )
        inner.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"), add="+")
        return inner

    # ── History ──────────────────────────────────────────────────────────

    def _build_history(self, page) -> None:
        import tkinter as tk

        bar = self._header(page, t("nav.history"))
        widgets.IconButton(bar, "trash", self._clear_history,
                           tooltip=t("history.delete_tip")).pack(side="right")
        widgets.IconButton(bar, "share", self._export_history,
                           tooltip=t("history.export_tip")).pack(side="right", padx=(0, 8))

        field = widgets.Card(page, radius=11, padx=14, pady=9)
        field.pack(fill="x", pady=(0, 16))
        glass = icons.photo(icons.image("search", 15, theme.TEXT_FAINT))
        self._images.append(glass)
        tk.Label(field.body, image=glass, bg=theme.CARD).pack(side="left", padx=(0, 10))
        self._search_var = tk.StringVar()
        search = theme.entry(field.body, self._search_var)
        search.pack(side="left", fill="x", expand=True)
        self._search_var.trace_add("write", lambda *_: self._refresh_history())
        self._search_is_placeholder = _placeholder(search, t("history.search"))

        self._history_body = self._scroll_area(page)

    def _refresh_history(self) -> None:
        import tkinter as tk

        body = self._history_body
        if body is None:
            return
        for child in body.winfo_children():
            child.destroy()

        query = "" if self._search_is_placeholder() else self._search_var.get().strip().lower()
        records = [r for r in reversed(self._store.all())
                   if not query or query in r.transcript.lower()]

        if not records:
            theme.label(body,
                        t("history.no_matches") if query else t("history.empty"),
                        size=10, colour=theme.TEXT_MUTED).pack(anchor="w", pady=8)
            return

        today = dt.date.today()
        current_group = None
        for record in records[:300]:
            group = _day_label(record.date, today)
            if group != current_group:
                current_group = group
                widgets.section_title(body, group).pack(anchor="w", pady=(14, 8))
            self._history_card(body, record)

        if len(records) > 300:
            theme.label(body, t("history.older_hidden", count=len(records) - 300),
                        size=8, colour=theme.TEXT_FAINT).pack(anchor="w", pady=(14, 0))

    def _history_card(self, parent, record) -> None:
        import tkinter as tk

        card = widgets.Card(parent, radius=12, padx=16, pady=13)
        card.pack(fill="x", pady=4)

        top = tk.Frame(card.body, bg=theme.CARD)
        top.pack(fill="x", pady=(0, 6))
        theme.label(top, record.date[11:16], size=9, colour=theme.TEXT_MUTED,
                    bg=theme.CARD).pack(side="left")
        if record.app:
            widgets.Chip(top, record.app[:26]).pack(side="left", padx=(10, 0))
        remove = theme.label(top, "\u2715", size=9, colour=theme.TEXT_FAINT,
                             bg=theme.CARD)
        remove.configure(cursor="hand2")
        remove.pack(side="right")
        remove.bind("<Button-1>", lambda _e, r=record: self._delete_record(r))
        remove.bind("<Enter>", lambda _e, w=remove: w.configure(fg=theme.ERROR))
        remove.bind("<Leave>", lambda _e, w=remove: w.configure(fg=theme.TEXT_FAINT))

        body = tk.Label(
            card.body, text=record.transcript, font=theme.font(10), bg=theme.CARD,
            fg=theme.TEXT, anchor="w", justify="left", wraplength=640,
        )
        body.pack(fill="x")
        # add="+" is load-bearing: Card binds its own auto-sizing handler to
        # this same event, and a plain bind() replaces it — which left every
        # card stuck at its initial height instead of hugging its text.
        card.body.bind(
            "<Configure>",
            lambda e, w=body: w.configure(wraplength=max(240, e.width - 8)),
            add="+",
        )
        for widget in (card.canvas, card.body, top, body):
            widget.bind("<Button-1>", lambda _e, r=record, c=card: self._copy_record(r, c))
            widget.configure(cursor="hand2")

    def _copy_record(self, record, card) -> None:
        import tkinter as tk

        if not injector.set_clipboard_text(record.transcript):
            return
        flash = tk.Label(card.body, text="Copied", font=theme.font(8, "bold"),
                         bg=theme.CARD, fg=theme.OK)
        flash.place(relx=1.0, y=0, anchor="ne")
        card.body.after(1100, flash.destroy)

    def _delete_record(self, record) -> str:
        """Remove one dictation. No confirmation: it is one row, the action is
        explicit, and a dialog for every deletion is worse than the mistake."""
        self._store.remove(record)
        self._refresh_history()
        return "break"  # don't let the click fall through to copy-the-card

    def _export_history(self) -> None:
        records = self._store.all()
        if not records:
            return
        target = paths.home() / "history-export.txt"
        lines = [f"{r.date}  {r.app or ''}\n{r.transcript}\n" for r in records]
        try:
            target.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            logger.warning("could not export history: %s", exc)
            return
        import os

        os.startfile(str(target))  # noqa: S606 - a file we just wrote

    def _clear_history(self) -> None:
        from tkinter import messagebox

        # History is the vault every delivery path writes to first; wiping it
        # is the one destructive thing this window can do.
        if not messagebox.askyesno(
            t("history.delete_title"),
            t("history.delete_body", count=len(self._store.all())),
            icon="warning", parent=self._window,
        ):
            return
        self._store.clear()
        self._refresh_history()

    # ── Insights ─────────────────────────────────────────────────────────

    def _build_insights(self, page) -> None:
        self._header(page, t("nav.insights"))
        self._insights_body = self._scroll_area(page)

    def _refresh_insights(self) -> None:
        import tkinter as tk

        body = self._insights_body
        if body is None:
            return
        for child in body.winfo_children():
            child.destroy()

        records = self._store.all()
        rows = metrics.read_all()

        tiles = tk.Frame(body, bg=theme.INK)
        tiles.pack(fill="x")
        for index, (glyph, value, caption, primary) in enumerate([
            ("chart", f"{stats.words_per_minute(records):.0f}", t("insights.wpm"), True),
            ("book", f"{stats.total_words(records):,}".replace(",", " "),
             t("insights.total_words"), False),
            ("bolt", f"{stats.streak_days(records)}", t("insights.streak"), False),
        ]):
            tiles.grid_columnconfigure(index, weight=1, uniform="tile")
            self._metric_tile(tiles, glyph, value, caption, primary).grid(
                row=0, column=index, sticky="ew", padx=(0 if index == 0 else 12, 0))

        self._activity_card(body, records)
        self._where_card(body, records)
        if rows:
            self._latency_card(body, rows)

    def _metric_tile(self, parent, glyph: str, value: str, caption: str, primary: bool):
        import tkinter as tk

        # Every tile is the same surface. The headline metric is marked by
        # colouring its number and its glyph, not by flooding a whole card in
        # accent — a filled block that size stops being emphasis and becomes
        # the only thing on the page.
        bg = theme.CARD
        card = widgets.Card(parent, bg=bg, radius=14, padx=18, pady=16)
        icon = icons.photo(icons.image(
            glyph, 16, theme.ACCENT if primary else theme.TEXT_MUTED))
        self._images.append(icon)
        tk.Label(card.body, image=icon, bg=bg).pack(anchor="w", pady=(0, 10))
        theme.label(card.body, value, size=24, display=True,
                    colour=theme.ACCENT if primary else theme.TEXT,
                    bg=bg).pack(anchor="w")
        theme.label(card.body, caption, size=8,
                    colour=theme.TEXT_FAINT, bg=bg).pack(anchor="w", pady=(2, 0))
        return card.frame

    def _activity_card(self, parent, records) -> None:
        import tkinter as tk

        card = widgets.Card(parent, radius=14, padx=18, pady=16)
        card.pack(fill="x", pady=(14, 0))
        theme.label(card.body, t("insights.last_30"), size=10, weight="bold",
                    bg=theme.CARD).pack(anchor="w", pady=(0, 12))

        today = dt.date.today()
        counts = {today - dt.timedelta(days=i): 0 for i in range(29, -1, -1)}
        for record in records:
            try:
                day = dt.datetime.fromisoformat(record.date).date()
            except ValueError:
                continue
            if day in counts:
                counts[day] += record.words

        chart = tk.Canvas(card.body, height=120, bg=theme.CARD,
                          highlightthickness=0, bd=0)
        chart.pack(fill="x")
        values = list(counts.values())

        def draw(_event=None) -> None:
            chart.delete("all")
            width = chart.winfo_width()
            if width < 10:
                return
            biggest = max(values) or 1
            slot = width / len(values)
            bar = max(3, slot * 0.55)
            for index, value in enumerate(values):
                x = index * slot + (slot - bar) / 2
                # A day with nothing still gets a stub, so the axis reads as a
                # timeline rather than as missing data.
                height = 4 if value == 0 else 8 + (value / biggest) * 100
                colour = theme.LINE if value == 0 else theme.ACCENT
                theme.rounded_rect(chart, x, 116 - height, x + bar, 116,
                                   min(3, bar / 2), fill=colour)

        chart.bind("<Configure>", draw)

    def _where_card(self, parent, records) -> None:
        import tkinter as tk

        by_app = stats.words_by_app(records)[:6]
        card = widgets.Card(parent, radius=14, padx=18, pady=16)
        card.pack(fill="x", pady=(14, 0))
        theme.label(card.body, t("insights.where"), size=10, weight="bold",
                    bg=theme.CARD).pack(anchor="w", pady=(0, 12))
        if not by_app:
            theme.label(card.body, t("insights.none_yet"), size=9,
                        colour=theme.TEXT_MUTED, bg=theme.CARD).pack(anchor="w")
            return
        biggest = by_app[0][1] or 1
        for name, words in by_app:
            row = tk.Frame(card.body, bg=theme.CARD)
            row.pack(fill="x", pady=4)
            theme.label(row, name[:30], size=9, colour=theme.TEXT_MUTED,
                        bg=theme.CARD).pack(side="left")
            theme.label(row, f"{words:,}".replace(",", " "), size=9,
                        colour=theme.TEXT_MUTED, bg=theme.CARD).pack(side="right")
            meter = tk.Canvas(row, height=8, bg=theme.CARD, highlightthickness=0, bd=0)
            meter.pack(side="left", fill="x", expand=True, padx=14)
            meter.bind(
                "<Configure>",
                lambda e, m=meter, w=words: (
                    m.delete("all"),
                    theme.rounded_rect(m, 0, 1, e.width - 1, 7, 3, fill=theme.CARD_HI),
                    theme.rounded_rect(m, 0, 1, max(6, (e.width - 1) * w / biggest), 7, 3,
                                       fill=theme.ACCENT),
                ),
            )

    def _latency_card(self, parent, rows) -> None:
        import tkinter as tk

        def median(values):
            values = sorted(values)
            if not values:
                return 0
            middle = len(values) // 2
            return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2

        typed = [r for r in rows if str(r.get("outcome", "")).startswith("pasted")]
        card = widgets.Card(parent, radius=14, padx=18, pady=16)
        card.pack(fill="x", pady=(14, 0))
        theme.label(card.body, t("insights.speed"), size=10, weight="bold", bg=theme.CARD).pack(
            anchor="w", pady=(0, 12))
        for caption, value in [
            (t("insights.median_total"), f"{median([r['total_ms'] for r in rows]):.0f} ms"),
            (t("insights.median_decode"), f"{median([r['stt_ms'] for r in rows]):.0f} ms"),
            (t("insights.typed_share"), f"{len(typed) * 100 // len(rows)}%"),
        ]:
            row = tk.Frame(card.body, bg=theme.CARD)
            row.pack(fill="x", pady=3)
            theme.label(row, caption, size=9, colour=theme.TEXT_MUTED,
                        bg=theme.CARD).pack(side="left")
            theme.label(row, value, size=9, weight="bold", bg=theme.CARD).pack(side="right")

    # ── Dictionary ───────────────────────────────────────────────────────

    def _build_dictionary(self, page) -> None:
        import tkinter as tk

        self._header(page, t("nav.dictionary"))
        caption = theme.label(page, t("dict.intro"), size=9,
                              colour=theme.TEXT_MUTED)
        caption.pack(anchor="w", fill="x", pady=(0, 14))
        # Without a wraplength tied to the page a long caption runs off the
        # right edge instead of flowing onto a second line.
        page.bind("<Configure>",
                  lambda e, w=caption: w.configure(wraplength=max(320, e.width - 8)),
                  add="+")

        card = widgets.Card(page, radius=14, padx=16, pady=14)
        card.pack(fill="both", expand=True)
        text = tk.Text(
            card.body, wrap="word", font=theme.mono(10), bg=theme.CARD, fg=theme.TEXT,
            relief="flat", bd=0, highlightthickness=0, insertbackground=theme.TEXT,
            height=14,
        )
        text.pack(fill="both", expand=True)
        self._dictionary_text = text

        footer = tk.Frame(page, bg=theme.INK)
        footer.pack(fill="x", pady=(14, 0))
        self._dictionary_status = theme.label(footer, "", size=9, colour=theme.TEXT_FAINT)
        self._dictionary_status.pack(side="left", pady=6)
        theme.button(footer, t("dict.save"), self._save_dictionary, primary=True).pack(side="right")

    def _refresh_dictionary(self) -> None:
        self._dictionary_text.delete("1.0", "end")
        self._dictionary_text.insert("1.0", "\n".join(dictionary.terms()))
        self._dictionary_status.configure(text="")

    def _save_dictionary(self) -> None:
        lines = [line.strip() for line in self._dictionary_text.get("1.0", "end").splitlines()]
        terms = [line for line in lines if line]
        dictionary.set_terms(terms)
        joined = ", ".join(terms)
        if len(joined) > dictionary.MAX_PROMPT_CHARS:
            # The prompt is capped, so say when the tail is being ignored
            # rather than letting the user wonder why term 200 never helps.
            self._dictionary_status.configure(
                text=t("dict.saved_capped", count=len(terms),
                       chars=dictionary.MAX_PROMPT_CHARS))
        else:
            self._dictionary_status.configure(text=t("dict.saved", count=len(terms)))

    # ── Settings ─────────────────────────────────────────────────────────

    def _build_settings(self, page) -> None:
        import tkinter as tk

        self._header(page, t("nav.settings"))
        body = self._scroll_area(page)

        # DICTATION
        widgets.section_title(body, t("settings.group_dictation")).pack(anchor="w", pady=(0, 8))
        card = widgets.Card(body, radius=14, padx=16, pady=8)
        card.pack(fill="x")
        row = widgets.SettingRow(card.body, "tap", TINT_BLUE, t("settings.activation"),
                                 t("settings.activation_hint"))
        widgets.Dropdown(row.control,
                         [("hold", t("settings.hold")), ("toggle", t("settings.toggle"))],
                         lambda: config.get_str("FVActivationMode"),
                         lambda v: config.set("FVActivationMode", v)).pack()
        row = widgets.SettingRow(card.body, "keyboard", TINT_INDIGO,
                                 t("settings.shortcut"), t("settings.shortcut_hint"))
        recorder = widgets.ShortcutRecorder(
            row.control, lambda: config.get_str("FVHotkey"), self._save_hotkey,
            on_listening=self._hotkey_capture,
            capture=self._app.capture_chord if self._app is not None else None)
        recorder.pack()
        # The whole row, not just the chip: this is the setting people most
        # want to change, and a click a pixel outside a 150 px target did
        # nothing at all.
        recorder.bind_clickable(row.frame, *row.frame.winfo_children())
        self._recorder = recorder
        self._hotkey_row = row
        row = widgets.SettingRow(card.body, "globe", TINT_TEAL, t("settings.language"))
        widgets.Dropdown(row.control, [("ru", t("settings.lang_ru")),
                                       ("en", t("settings.lang_en")),
                                       ("auto", t("settings.lang_auto"))],
                         lambda: config.get_str("FVLanguage"),
                         self._set_language).pack()
        row = widgets.SettingRow(card.body, "mic", TINT_PINK, t("settings.microphone"),
                                 last=True)
        widgets.Dropdown(row.control, _microphones(),
                         lambda: config.get_str("FVMicrophone"),
                         lambda v: config.set("FVMicrophone", v)).pack()

        # TEXT PROCESSING
        widgets.section_title(body, t("settings.group_text")).pack(anchor="w", pady=(20, 8))
        card = widgets.Card(body, radius=14, padx=16, pady=8)
        card.pack(fill="x")
        self._switch_row(card.body, "bolt", TINT_ORANGE, t("settings.streaming"),
                         t("settings.streaming_hint"), "FVStreaming")
        self._switch_row(card.body, "sparkle", TINT_INDIGO, t("settings.cleanup"),
                         t("settings.cleanup_hint"), "FVCleanupEnabled")
        row = widgets.SettingRow(card.body, "chip", TINT_GREY,
                                 t("settings.cleanup_model"),
                                 t("settings.cleanup_model_hint"))
        # Re-asked every time the menu opens: Ollama shuts itself down when
        # idle, and a list built once at window-open time showed only the
        # model already configured.
        widgets.Dropdown(row.control, _ollama_models(),
                         lambda: config.get_str("FVOllamaModel"),
                         lambda v: config.set("FVOllamaModel", v),
                         refresh=_ollama_models).pack()
        row = widgets.SettingRow(card.body, "clock", TINT_ORANGE,
                                 t("settings.keep_alive"),
                                 t("settings.keep_alive_hint"))
        widgets.Dropdown(row.control,
                         [("0", t("settings.keep_alive_never")),
                          ("5m", t("settings.keep_alive_5m")),
                          ("1h", t("settings.keep_alive_1h")),
                          ("24h", t("settings.keep_alive_always"))],
                         lambda: config.get_str("FVOllamaKeepAlive"),
                         lambda v: config.set("FVOllamaKeepAlive", v)).pack()
        self._switch_row(card.body, "wand", TINT_GREEN, t("settings.smartfix"),
                         t("settings.smartfix_hint"), "FVSmartFix")
        self._switch_row(card.body, "keyboard", TINT_BLUE,
                         t("settings.voice_commands"),
                         t("settings.voice_commands_hint"), "FVVoiceCommands",
                         last=True)
        self._ollama_status = theme.label(body, t("ollama.checking"), size=8,
                                          colour=theme.TEXT_FAINT)
        self._ollama_status.pack(anchor="w", pady=(8, 0))
        theme.label(body, t("settings.cleanup_note"), size=8,
                    colour=theme.TEXT_FAINT).pack(anchor="w", pady=(2, 0))

        # GENERAL
        widgets.section_title(body, t("settings.group_general")).pack(anchor="w", pady=(20, 8))
        card = widgets.Card(body, radius=14, padx=16, pady=8)
        card.pack(fill="x")
        row = widgets.SettingRow(card.body, "power", TINT_GREEN,
                                 t("settings.launch_at_login"))
        self._login_switch = widgets.Switch(
            row.control, shortcut.launches_at_login,
            lambda v: shortcut.set_launch_at_login(v))
        self._login_switch.pack()
        self._switch_row(card.body, "speaker", TINT_BLUE, t("settings.sounds"), "",
                         "FVSounds")
        self._switch_row(card.body, "mic", TINT_PINK, t("settings.overlay"),
                         t("settings.overlay_hint"), "FVOverlay")
        row = widgets.SettingRow(card.body, "chip", TINT_TEAL,
                                 t("settings.whisper_model"),
                                 t("settings.whisper_model_hint"))
        widgets.Dropdown(row.control, _WHISPER_MODELS,
                         lambda: config.get_str("FVModel"), self._set_whisper_model).pack()
        self._switch_row(card.body, "clipboard", TINT_GREY, t("settings.clipboard"),
                         t("settings.clipboard_hint"), "FVPasteViaClipboard")
        row = widgets.SettingRow(card.body, "globe", TINT_INDIGO,
                                 t("settings.ui_language"),
                                 t("settings.ui_language_hint"), last=True)
        widgets.Dropdown(row.control,
                         [("auto", t("settings.ui_auto")), ("ru", "Русский"),
                          ("en", "English")],
                         lambda: config.get_str("FVUILanguage"),
                         self._set_ui_language).pack()

        footer = tk.Frame(body, bg=theme.INK)
        footer.pack(fill="x", pady=(20, 0))
        theme.label(footer, t("settings.data_location", path=paths.home()), size=8,
                    colour=theme.TEXT_FAINT).pack(side="left", pady=6)
        theme.button(footer, t("settings.open_folder"),
                     self._open_folder).pack(side="right")

    def _switch_row(self, parent, glyph: str, tint: str, title: str, subtitle: str,
                    key: str, last: bool = False) -> None:
        row = widgets.SettingRow(parent, glyph, tint, title, subtitle, last=last)
        switch = widgets.Switch(row.control, lambda: config.get_bool(key),
                                lambda v: self._set_flag(key, v))
        switch.pack()

    def _set_flag(self, key: str, value: bool) -> None:
        config.set(key, value)
        if key in ("FVCleanupEnabled", "FVSmartFix") and value and self._app:
            self._app.cleaner.warmup()

    def _set_language(self, value: str) -> None:
        config.set("FVLanguage", value)
        if self._app:
            self._app.transcriber.reset_session_language()

    def _hotkey_capture(self, listening: bool) -> None:
        """Silence the global hotkey while a new one is being recorded.

        The chord a user reaches for first is the one already configured, and
        the hook would swallow it and start a dictation instead of recording
        the key.
        """
        # Ctrl and Alt are accepted, but only when held — say so at the moment
        # the user is actually choosing, or a held Ctrl reads as "not working".
        row = getattr(self, "_hotkey_row", None)
        if row is not None and row.subtitle is not None:
            row.subtitle.configure(text=t("settings.shortcut_modifier_hint")
                                   if listening else t("settings.shortcut_hint"))
        if self._app is None:
            return
        if not listening:
            # capture_chord() paused it on the way in; put it back, picking up
            # whatever was just saved.
            self._app.resume_hotkey()

    def _save_hotkey(self, value: str) -> bool:
        """Validate, store, and rebind the hook immediately.

        Applying it live is the point: a shortcut the user cannot try until
        after a restart is one they cannot tell is wrong.
        """
        from tkinter import messagebox

        from ..hotkey import parse

        value = value.strip()
        if not value or value == config.get_str("FVHotkey"):
            return False
        try:
            parse(value)
        except ValueError as exc:
            messagebox.showwarning(t("settings.shortcut_bad_title"), str(exc),
                                   parent=self._window)
            return False
        config.set("FVHotkey", value)
        if self._app is not None:
            self._app.rebind_hotkey()
        return True

    def _set_ui_language(self, value: str) -> None:
        """Store it and say a restart is needed.

        Re-translating live would mean rebuilding every window, and half the
        strings are baked into module constants at import (the pill sizes
        itself from its longest label). A restart is honest and instant.
        """
        from tkinter import messagebox

        if value == config.get_str("FVUILanguage"):
            return
        config.set("FVUILanguage", value)
        messagebox.showinfo(t("settings.restart_needed"),
                            t("settings.ui_language_hint"), parent=self._window)

    def _set_whisper_model(self, value: str) -> None:
        """Switching model reloads it in the background. The alternative is a
        setting that appears to do nothing until the next launch."""
        if value == config.get_str("FVModel"):
            return
        config.set("FVModel", value)
        if self._app is not None:
            self._app.reload_model()

    def _refresh_settings(self) -> None:
        # Switches read config on every paint, but Launch-at-login reads the
        # filesystem, which something else may have changed.
        if hasattr(self, "_login_switch"):
            self._login_switch.paint()
        self._refresh_ollama_status()

    def _refresh_ollama_status(self) -> None:
        """Say whether the cleanup model is actually reachable.

        Cleanup degrades silently by design — every failure path returns the
        raw transcript — which is right for a dictation app and terrible for
        discoverability: with Ollama not running, the switch reads ON and
        nothing it promises happens. Ollama does not install a startup entry
        on Windows, so "not running" is the normal state after a reboot.

        Probed on a worker thread: the HTTP call has a timeout, and a frozen
        Settings page while it runs would be its own bug.
        """
        import threading

        from . import ui

        if not hasattr(self, "_ollama_status"):
            return
        if not config.get_bool("FVCleanupEnabled") and not config.get_bool("FVSmartFix"):
            self._ollama_status.configure(text=t("ollama.off"), fg=theme.TEXT_FAINT)
            return

        wanted = config.get_str("FVOllamaModel")

        def probe() -> None:
            from .. import cleaner

            models = cleaner.installed_models()
            if not models:
                text = t("ollama.not_running")
                colour = theme.PROCESSING
            elif wanted not in models:
                text = t("ollama.model_missing", model=wanted)
                colour = theme.PROCESSING
            else:
                text = t("ollama.ready", model=wanted)
                colour = theme.OK
            ui.call(lambda: self._set_ollama_status(text, colour))

        threading.Thread(target=probe, name="ollama-probe", daemon=True).start()

    def _set_ollama_status(self, text: str, colour: str) -> None:
        if hasattr(self, "_ollama_status") and self._ollama_status.winfo_exists():
            self._ollama_status.configure(text=text, fg=colour)

    @staticmethod
    def _open_folder() -> None:
        import os

        os.startfile(str(paths.home()))  # noqa: S606 - our own folder


# Whisper sizes faster-whisper can fetch by name, smallest first so the list
# reads as a speed/accuracy dial.
_WHISPER_MODELS = [
    ("tiny", "tiny - fastest, roughest"),
    ("base", "base"),
    ("small", "small"),
    ("medium", "medium"),
    ("large-v3-turbo", "large-v3-turbo - recommended"),
    ("large-v3", "large-v3 - most accurate, slowest"),
]


def _microphones() -> list[tuple[str, str]]:
    """Real capture devices, with the system default first.

    The setting stores a name fragment rather than an index on purpose:
    indices are reassigned as devices come and go, so a saved index quietly
    starts pointing at a different microphone.
    """
    from .. import audio

    options = [("", "System default")]
    try:
        for _index, name in audio.input_devices():
            options.append((name, name if len(name) <= 42 else name[:41] + "..."))
    except Exception:  # noqa: BLE001 - a broken enumeration must not break Settings
        logger.debug("could not list microphones", exc_info=True)
    return options


def _ollama_models() -> list[tuple[str, str]]:
    """Whatever Ollama has actually pulled, plus whatever is configured.

    Listing only installed models avoids offering a choice that would fail at
    the first dictation; keeping the configured value in the list means a
    model Ollama cannot currently report still shows as selected instead of
    reading as blank.
    """
    from .. import cleaner

    current = config.get_str("FVOllamaModel")
    names: list[str] = []
    try:
        names = cleaner.installed_models()
    except Exception:  # noqa: BLE001
        logger.debug("could not list Ollama models", exc_info=True)
    if current and current not in names:
        names.insert(0, current)
    if not names:
        names = [current or "qwen2.5:3b"]
    return [(name, name) for name in names]


def _remembered_geometry() -> str:
    """Where the window was last time, if that is still on a real screen.

    A geometry saved on a monitor that has since been unplugged would put the
    window off-screen with no way to reach it, so the position is dropped
    unless its top-left corner still lands inside a work area.
    """
    saved = config.get_str("FVWindowGeometry")
    default = f"{WIDTH}x{HEIGHT}"
    if "+" not in saved:
        return default
    try:
        size, x, y = saved.split("+", 2)
        left, top, right, bottom = winapi.work_area_of_window(0)
        px_, py = int(x), int(y)
        visible_anywhere = (
            left - 40 <= px_ <= right - 80 and top - 10 <= py <= bottom - 60
        )
        if not visible_anywhere:
            return size or default
        return saved
    except Exception:  # noqa: BLE001 - a corrupt value must not stop the window
        return default


def _day_label(iso: str, today: dt.date) -> str:
    try:
        day = dt.datetime.fromisoformat(iso).date()
    except ValueError:
        return t("history.earlier")
    if day == today:
        return t("history.today")
    if day == today - dt.timedelta(days=1):
        return t("history.yesterday")
    if (today - day).days < 7:
        return t(f"date.weekday_{day.weekday()}")
    return f"{day.day} {t(f'date.month_{day.month}')} {day.year}"


def _placeholder(entry, text: str):
    """Grey hint text that clears on focus and returns when left empty.

    Returns a predicate the caller MUST consult before treating the field's
    contents as input: inserting the placeholder fires the same change
    notification a keystroke does, so without it a freshly opened window would
    filter the history by the literal words "Search dictations".
    """
    state = {"showing": False}

    def show() -> None:
        if entry.get():
            return
        state["showing"] = True
        entry.insert(0, text)
        entry.configure(fg=theme.TEXT_MUTED)

    def clear(_event=None) -> None:
        if state["showing"]:
            state["showing"] = False
            entry.delete(0, "end")
        entry.configure(fg=theme.TEXT)

    entry.bind("<FocusIn>", clear)
    entry.bind("<FocusOut>", lambda _e: show())
    show()
    return lambda: state["showing"]


window = MainWindow()
