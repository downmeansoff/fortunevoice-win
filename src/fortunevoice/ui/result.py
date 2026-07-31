"""The result panel.

Port of Sources/FortuneVoice/ResultPanel.swift. When a transcript can't be
typed — the user switched windows, there is no editable field, SendInput
failed — it must not vanish into a log line. macOS showed a floating panel
with a Copy button; the first cut of this port showed a tray balloon, which
truncates, disappears on its own schedule, and can't be copied from.

Non-activating like the pill, for the same reason: this appears *after* a
dictation, and stealing focus at that moment would be its own bug. It is not
click-through, though — the whole point is the Copy button.

Nothing is put on the clipboard until the user asks. That was the reason the
macOS build stopped routing dictations through the clipboard at all, and a
panel that "helpfully" pre-copied would quietly undo it.
"""

from __future__ import annotations

from .. import injector
from ..log import get as get_logger
from . import theme, ui

logger = get_logger("ui.result")

WIDTH = theme.px(460)
HEIGHT = theme.px(178)
MARGIN = theme.px(24)
# Long enough to read a sentence and reach for the mouse, short enough that a
# forgotten panel doesn't sit over the user's work.
AUTO_HIDE_MS = 20_000


class ResultPanel:
    def __init__(self) -> None:
        self._window = None
        self._hint_label = None
        self._text = None
        self._copy_button = None
        self._after_id = None
        self._content = ""

    def show(self, text: str, hint: str) -> None:
        ui.call(lambda: self._show(text, hint))

    def hide(self) -> None:
        ui.call(self._hide)

    # ── UI thread only ───────────────────────────────────────────────────

    def _build(self) -> None:
        import tkinter as tk

        from .. import winapi

        window = tk.Toplevel(ui.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=theme.INK)
        window.withdraw()

        border = tk.Frame(window, bg=theme.LINE)
        border.pack(fill="both", expand=True)
        body = tk.Frame(border, bg=theme.INK)
        body.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(body, bg=theme.INK)
        header.pack(fill="x", padx=18, pady=(14, 6))
        self._hint_label = theme.label(header, "", size=9, colour=theme.PROCESSING)
        self._hint_label.pack(side="left")
        close = tk.Label(header, text="✕", bg=theme.INK, fg=theme.TEXT_FAINT,
                         font=theme.font(9), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self._hide())

        self._text = tk.Text(
            body, height=4, wrap="word", font=theme.font(10),
            bg=theme.CARD, fg=theme.TEXT, relief="flat", bd=0,
            padx=12, pady=10, highlightthickness=0, insertbackground=theme.TEXT,
        )
        self._text.pack(fill="both", expand=True, padx=18)

        footer = tk.Frame(body, bg=theme.INK)
        footer.pack(fill="x", padx=18, pady=12)
        self._copy_button = theme.button(footer, "Copy", self._copy, primary=True)
        self._copy_button.pack(side="right")
        theme.label(footer, "Saved to History", size=8, colour=theme.TEXT_FAINT).pack(
            side="left", pady=6
        )

        self._window = window
        window.update_idletasks()
        winapi.make_non_activating(window)

    def _place(self) -> None:
        from .. import winapi

        # Bottom-right of the monitor the user is on, where Windows puts its
        # own notifications, and clear of the pill.
        left, top, right, bottom = winapi.work_area_of_window(winapi.foreground_window())
        x = right - WIDTH - MARGIN
        y = bottom - HEIGHT - MARGIN
        self._window.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _show(self, text: str, hint: str) -> None:
        from .. import winapi

        if self._window is None:
            self._build()
        self._content = text
        self._hint_label.configure(text=hint)
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", text)
        # Read-only, but still selectable so the user can grab part of it.
        self._text.configure(state="disabled")
        self._copy_button.configure(text="Copy", bg=theme.ACCENT)

        self._place()
        self._window.deiconify()
        winapi.raise_without_focus(self._window)

        if self._after_id:
            self._window.after_cancel(self._after_id)
        self._after_id = self._window.after(AUTO_HIDE_MS, self._hide)

    def _hide(self) -> None:
        if self._window is None:
            return
        if self._after_id:
            self._window.after_cancel(self._after_id)
            self._after_id = None
        self._window.withdraw()

    def _copy(self) -> None:
        if injector.set_clipboard_text(self._content):
            self._copy_button.configure(text="Copied", bg=theme.OK)
        else:
            self._copy_button.configure(text="Failed", bg=theme.ERROR)
            logger.warning("could not put the transcript on the clipboard")


panel = ResultPanel()
