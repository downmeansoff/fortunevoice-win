"""The main window: History, Stats, Dictionary, Settings.

Everything the app already stores but had no way to show. History and metrics
were JSON files you had to open in an editor; the dictionary and the settings
were a file you had to know the key names for.

Rendering choices worth stating, because they look lazy and are not:

* **History is a Text widget, not a list of card frames.** A Text gives native
  selection and Ctrl+C across records, find-as-you-type highlighting through
  tags, and smooth scrolling over thousands of rows — all of which a stack of
  Frames would need reimplementing, badly.
* **ttk is avoided.** On Windows the native theme engine overrides background
  colours on ttk widgets, so a dark ttk.Notebook comes out grey. The tab strip
  here is four labels and a bound click.
"""

from __future__ import annotations

from .. import config, dictionary, metrics, paths, stats
from ..log import get as get_logger
from ..store import DictationStore
from . import theme, ui

logger = get_logger("ui.main")

WIDTH = 860
HEIGHT = 600

TABS = ("History", "Stats", "Dictionary", "Settings")

# (key, label, kind, hint). Only the settings worth changing without reading
# the docs; the rest stay in config.json.
SETTINGS = [
    ("FVHotkey", "Hotkey", "text", "e.g. ctrl+alt+space, f9, rctrl"),
    ("FVActivationMode", "Activation", "choice:hold,toggle", "hold = push-to-talk"),
    ("FVLanguage", "Language", "choice:ru,en,auto", "auto detects per dictation"),
    ("FVModel", "Whisper model", "text", "restart to apply"),
    ("FVMicrophone", "Microphone", "text", "name fragment; empty = system default"),
    ("FVStreaming", "Transcribe while speaking", "bool", "halves the wait on long dictations"),
    ("FVCleanupEnabled", "AI cleanup (Ollama)", "bool", "rewrites filler and punctuation"),
    ("FVSmartFix", "Auto-fix garbled words", "bool", "only on low-confidence decodes"),
    ("FVOllamaModel", "Cleanup model", "text", "qwen2.5:3b recommended"),
    ("FVMiniPrompt", "Short prompt for short phrases", "bool", "turn off on a fast model"),
    ("FVSounds", "Sounds", "bool", ""),
    ("FVPasteViaClipboard", "Paste via clipboard", "bool", "only for apps that ignore typing"),
]


class MainWindow:
    def __init__(self, store: DictationStore | None = None) -> None:
        self._store = store or DictationStore()
        self._window = None
        self._tab = "History"
        self._tab_labels: dict[str, object] = {}
        self._pages: dict[str, object] = {}
        self._search_var = None
        self._history_text = None
        self._dictionary_text = None
        self._status = None

    def show(self) -> None:
        ui.call(self._show)

    # ── UI thread only ───────────────────────────────────────────────────

    def _show(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._build()
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()
        self._select_tab(self._tab)

    def _build(self) -> None:
        import tkinter as tk

        from .. import assets

        window = tk.Toplevel(ui.root)
        window.title("FortuneVoice")
        window.geometry(f"{WIDTH}x{HEIGHT}")
        window.minsize(680, 460)
        window.configure(bg=theme.INK)
        try:
            window.iconbitmap(str(assets.icon_path()))
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        # Closing hides: the app lives in the tray, and destroying the window
        # would throw away the loaded history for no reason.
        window.protocol("WM_DELETE_WINDOW", lambda: window.withdraw())

        header = tk.Frame(window, bg=theme.INK)
        header.pack(fill="x", padx=22, pady=(18, 0))
        theme.label(header, "FortuneVoice", size=15, weight="bold").pack(side="left")
        self._status = theme.label(header, "", size=9, colour=theme.TEXT_FAINT)
        self._status.pack(side="right", pady=6)

        tabs = tk.Frame(window, bg=theme.INK)
        tabs.pack(fill="x", padx=22, pady=(14, 0))
        for name in TABS:
            label = tk.Label(
                tabs, text=name, font=theme.font(10), bg=theme.INK,
                fg=theme.TEXT_MUTED, cursor="hand2", padx=2, pady=6,
            )
            label.pack(side="left", padx=(0, 22))
            label.bind("<Button-1>", lambda _e, n=name: self._select_tab(n))
            self._tab_labels[name] = label
        tk.Frame(window, bg=theme.LINE, height=1).pack(fill="x", padx=22, pady=(0, 0))

        container = tk.Frame(window, bg=theme.INK)
        container.pack(fill="both", expand=True, padx=22, pady=16)
        self._container = container
        self._window = window

        self._pages["History"] = self._build_history(container)
        self._pages["Stats"] = self._build_stats(container)
        self._pages["Dictionary"] = self._build_dictionary(container)
        self._pages["Settings"] = self._build_settings(container)

    def _select_tab(self, name: str) -> None:
        self._tab = name
        for tab_name, label in self._tab_labels.items():
            active = tab_name == name
            label.configure(fg=theme.TEXT if active else theme.TEXT_MUTED,
                            font=theme.font(10, "bold" if active else "normal"))
        for page_name, page in self._pages.items():
            page.pack_forget() if page_name != name else page.pack(fill="both", expand=True)
        refresh = getattr(self, f"_refresh_{name.lower()}", None)
        if refresh:
            refresh()

    # ── History ──────────────────────────────────────────────────────────

    def _build_history(self, parent):
        import tkinter as tk

        page = tk.Frame(parent, bg=theme.INK)

        bar = tk.Frame(page, bg=theme.INK)
        bar.pack(fill="x", pady=(0, 12))
        self._search_var = tk.StringVar()
        search = theme.entry(bar, self._search_var)
        search.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 10))
        self._search_var.trace_add("write", lambda *_: self._refresh_history())
        # Placeholder rather than a label above: the bar is one row tall and an
        # empty dark slab reads as broken rather than as "type here".
        self._search_is_placeholder = _placeholder(search, "Search dictations")
        theme.button(bar, "Refresh", self._refresh_history).pack(side="left")

        wrap = tk.Frame(page, bg=theme.INK)
        wrap.pack(fill="both", expand=True)
        text = tk.Text(
            wrap, wrap="word", font=theme.font(10), bg=theme.INK_RAISED,
            fg=theme.TEXT, relief="flat", bd=0, padx=16, pady=14,
            highlightthickness=0, spacing1=2, spacing3=10, cursor="arrow",
        )
        bar_scroll = theme.scrollbar(wrap, text.yview)
        text.configure(yscrollcommand=bar_scroll.set)
        bar_scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        text.tag_configure("meta", foreground=theme.TEXT_FAINT, font=theme.font(8))
        text.tag_configure("body", foreground=theme.TEXT, font=theme.font(10))
        text.tag_configure("hit", background="#3A3F52", foreground=theme.TEXT)
        text.tag_configure("empty", foreground=theme.TEXT_MUTED, font=theme.font(10))
        self._history_text = text
        return page

    def _refresh_history(self) -> None:
        text = self._history_text
        if text is None:
            return
        query = "" if self._search_is_placeholder() else (self._search_var.get() or "").strip().lower()
        records = list(reversed(self._store.all()))
        if query:
            records = [r for r in records if query in r.transcript.lower()]

        text.configure(state="normal")
        text.delete("1.0", "end")
        if not records:
            text.insert("end", "Nothing here yet.\n" if not query else "No matches.\n", "empty")
        for record in records[:500]:
            when = record.date.replace("T", " ")[:16]
            app = f" · {record.app}" if record.app else ""
            text.insert("end", f"{when}{app} · {record.words} words\n", "meta")
            text.insert("end", f"{record.transcript}\n", "body")
        text.configure(state="disabled")

        if query:
            self._highlight(text, query)
        shown = min(len(records), 500)
        self._status.configure(
            text=f"{shown} of {len(records)} shown" if len(records) > 500
            else f"{len(records)} dictations"
        )

    @staticmethod
    def _highlight(text, query: str) -> None:
        text.tag_remove("hit", "1.0", "end")
        index = "1.0"
        while True:
            index = text.search(query, index, nocase=True, stopindex="end")
            if not index:
                return
            end = f"{index}+{len(query)}c"
            text.tag_add("hit", index, end)
            index = end

    # ── Stats ────────────────────────────────────────────────────────────

    def _build_stats(self, parent):
        import tkinter as tk

        page = tk.Frame(parent, bg=theme.INK)
        self._stats_tiles = tk.Frame(page, bg=theme.INK)
        self._stats_tiles.pack(fill="x")
        self._stats_detail = tk.Frame(page, bg=theme.INK)
        self._stats_detail.pack(fill="both", expand=True, pady=(20, 0))
        return page

    def _refresh_stats(self) -> None:
        import tkinter as tk

        for frame in (self._stats_tiles, self._stats_detail):
            for child in frame.winfo_children():
                child.destroy()

        records = self._store.all()
        rows = metrics.read_all()
        typed = [r for r in rows if str(r.get("outcome", "")).startswith("pasted")]

        def median(values):
            values = sorted(values)
            if not values:
                return 0
            middle = len(values) // 2
            return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2

        tiles = [
            ("Dictations", f"{len(records)}"),
            ("Words", f"{stats.total_words(records):,}".replace(",", " ")),
            ("Words / minute", f"{stats.words_per_minute(records):.0f}"),
            ("Day streak", f"{stats.streak_days(records)}"),
            ("Median wait", f"{median([r['total_ms'] for r in rows]):.0f} ms" if rows else "—"),
            ("Typed straight in", f"{len(typed) * 100 // len(rows)}%" if rows else "—"),
        ]
        for index, (caption, value) in enumerate(tiles):
            tile = tk.Frame(self._stats_tiles, bg=theme.INK_RAISED)
            tile.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0, 12), pady=(0, 12))
            self._stats_tiles.grid_columnconfigure(index % 3, weight=1)
            inner = tk.Frame(tile, bg=theme.INK_RAISED)
            inner.pack(fill="x", padx=18, pady=14)
            theme.label(inner, value, size=20, weight="bold").pack(anchor="w")
            theme.label(inner, caption, size=9, colour=theme.TEXT_MUTED).pack(anchor="w")

        by_app = stats.words_by_app(records)[:8]
        theme.label(self._stats_detail, "Where the words went", size=11, weight="bold").pack(anchor="w")
        if not by_app:
            theme.label(self._stats_detail, "No dictations yet.", size=10,
                        colour=theme.TEXT_MUTED).pack(anchor="w", pady=(10, 0))
            return
        biggest = by_app[0][1] or 1
        chart = tk.Frame(self._stats_detail, bg=theme.INK)
        chart.pack(fill="x", pady=(12, 0))
        for name, words in by_app:
            row = tk.Frame(chart, bg=theme.INK)
            row.pack(fill="x", pady=3)
            theme.label(row, name[:44], size=9, colour=theme.TEXT_MUTED).pack(side="left")
            theme.label(row, f"{words}", size=9, colour=theme.TEXT_FAINT).pack(side="right")
            meter = tk.Frame(row, bg=theme.INK_RAISED, height=6)
            meter.pack(side="left", fill="x", expand=True, padx=12)
            fill = tk.Frame(meter, bg=theme.ACCENT, height=6)
            fill.place(relwidth=words / biggest, relheight=1)

    # ── Dictionary ───────────────────────────────────────────────────────

    def _build_dictionary(self, parent):
        import tkinter as tk

        page = tk.Frame(parent, bg=theme.INK)
        theme.label(
            page,
            "Names and jargon Whisper keeps mishearing — one per line.\n"
            "Fed to the decoder as a prompt, and to the cleanup model as preferred spellings.",
            size=9, colour=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 12))

        text = tk.Text(
            page, wrap="word", font=theme.mono(10), bg=theme.INK_RAISED, fg=theme.TEXT,
            relief="flat", bd=0, padx=14, pady=12, highlightthickness=0,
            insertbackground=theme.TEXT,
        )
        text.pack(fill="both", expand=True)
        self._dictionary_text = text

        footer = tk.Frame(page, bg=theme.INK)
        footer.pack(fill="x", pady=(12, 0))
        self._dictionary_status = theme.label(footer, "", size=9, colour=theme.TEXT_FAINT)
        self._dictionary_status.pack(side="left", pady=6)
        theme.button(footer, "Save", self._save_dictionary, primary=True).pack(side="right")
        return page

    def _refresh_dictionary(self) -> None:
        self._dictionary_text.delete("1.0", "end")
        self._dictionary_text.insert("1.0", "\n".join(dictionary.terms()))
        self._dictionary_status.configure(text="")

    def _save_dictionary(self) -> None:
        lines = [line.strip() for line in self._dictionary_text.get("1.0", "end").splitlines()]
        terms = [line for line in lines if line]
        dictionary.set_terms(terms)
        # The prompt is capped, so say when the tail is being ignored rather
        # than letting the user wonder why term 200 never helps.
        joined = ", ".join(terms)
        if len(joined) > dictionary.MAX_PROMPT_CHARS:
            self._dictionary_status.configure(
                text=f"Saved {len(terms)} terms — only the first "
                     f"{dictionary.MAX_PROMPT_CHARS} characters are sent to the model.",
            )
        else:
            self._dictionary_status.configure(text=f"Saved {len(terms)} terms.")

    # ── Settings ─────────────────────────────────────────────────────────

    def _build_settings(self, parent):
        import tkinter as tk

        page = tk.Frame(parent, bg=theme.INK)
        canvas = tk.Canvas(page, bg=theme.INK, highlightthickness=0, bd=0)
        scroll = theme.scrollbar(page, canvas.yview)
        inner = tk.Frame(canvas, bg=theme.INK)
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._setting_widgets = {}
        for key, label_text, kind, hint in SETTINGS:
            row = tk.Frame(inner, bg=theme.INK)
            # Right padding clears the scrollbar: the inner frame is stretched
            # to the full canvas width, so a control packed hard right lands
            # underneath it and gets visually clipped.
            row.pack(fill="x", pady=7, padx=(0, 16))
            left = tk.Frame(row, bg=theme.INK)
            left.pack(side="left", fill="x", expand=True)
            theme.label(left, label_text, size=10).pack(anchor="w")
            if hint:
                theme.label(left, hint, size=8, colour=theme.TEXT_FAINT).pack(anchor="w")
            self._setting_widgets[key] = self._control(row, key, kind)

        tk.Frame(inner, bg=theme.LINE, height=1).pack(fill="x", pady=16, padx=(0, 16))
        footer = tk.Frame(inner, bg=theme.INK)
        footer.pack(fill="x", padx=(0, 16))
        theme.label(footer, f"Everything lives in {paths.home()}", size=8,
                    colour=theme.TEXT_FAINT).pack(side="left", pady=6)
        theme.button(footer, "Open data folder", self._open_folder).pack(side="right")
        return page

    def _control(self, row, key: str, kind: str):
        import tkinter as tk

        if kind == "bool":
            widget = tk.Label(
                row, font=theme.font(9, "bold"), width=5, cursor="hand2",
                relief="flat", bd=0, padx=10, pady=5,
            )

            def toggle(_event=None) -> None:
                config.set(key, not config.get_bool(key))
                paint()

            def paint() -> None:
                on = config.get_bool(key)
                widget.configure(text="ON" if on else "OFF",
                                 bg=theme.OK if on else theme.INK_RAISED,
                                 fg="#0B0D12" if on else theme.TEXT_MUTED)

            widget.bind("<Button-1>", toggle)
            paint()
            widget.pack(side="right")
            return widget

        if kind.startswith("choice:"):
            options = kind.split(":", 1)[1].split(",")
            holder = tk.Frame(row, bg=theme.INK)
            holder.pack(side="right")
            chips = {}

            def choose(value: str) -> None:
                config.set(key, value)
                paint()

            def paint() -> None:
                current = config.get_str(key)
                for value, chip in chips.items():
                    active = value == current
                    chip.configure(bg=theme.ACCENT if active else theme.INK_RAISED,
                                   fg="#0B0D12" if active else theme.TEXT_MUTED)

            for value in options:
                chip = tk.Label(holder, text=value, font=theme.font(9), cursor="hand2",
                                padx=10, pady=5)
                chip.pack(side="left", padx=(6, 0))
                chip.bind("<Button-1>", lambda _e, v=value: choose(v))
                chips[value] = chip
            paint()
            return holder

        variable = tk.StringVar(value=config.get_str(key))
        field = theme.entry(row, variable)
        field.configure(width=26)
        field.pack(side="right", ipady=5)
        field.bind("<FocusOut>", lambda _e: config.set(key, variable.get()))
        field.bind("<Return>", lambda _e: config.set(key, variable.get()))
        return field

    def _refresh_settings(self) -> None:
        pass  # controls read config on every paint

    @staticmethod
    def _open_folder() -> None:
        import os

        os.startfile(str(paths.home()))  # noqa: S606 - our own folder


def _placeholder(entry, text: str):
    """Grey hint text that clears on focus and comes back when left empty.

    Returns a predicate the caller MUST consult before treating the field's
    contents as input: inserting the placeholder fires the same change
    notification a keystroke does, so without it a freshly opened window would
    immediately filter the history by the literal words "Search dictations"
    and show nothing.
    """
    state = {"showing": False}

    def show() -> None:
        if entry.get():
            return
        state["showing"] = True
        entry.insert(0, text)
        entry.configure(fg=theme.TEXT_FAINT)

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
