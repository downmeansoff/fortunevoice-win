"""The result panel.

Port of Sources/FortuneVoice/ResultPanel.swift. When a transcript can't be
typed (the user switched windows, there is no editable field, SendInput
failed), it must not vanish into a log line. macOS showed a floating panel
with a Copy button; the first cut of this port showed a tray balloon, which
truncates, disappears on its own schedule, and can't be copied from.

Non-activating like the pill, for the same reason: this appears *after* a
dictation, and stealing focus at that moment would be its own bug. It is not
click-through, though: the whole point is the Copy button.

Nothing is put on the clipboard until the user asks. That was the reason the
macOS build stopped routing dictations through the clipboard at all, and a
panel that "helpfully" pre-copied would quietly undo it.
"""

from __future__ import annotations

from .. import injector
from ..log import get as get_logger
from ..strings import t
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
        # Set by App._hold, so the panel can act on the transcript it is
        # showing rather than only describe it.
        self._app = None
        self._content = ""

    def show(self, text: str, hint: str, app=None) -> None:
        self._app = app or self._app
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
        # The commonest reason this panel exists is "you switched windows",
        # so the thing the user wants is the text in the window they are in
        # NOW. `App.retype_last` does exactly that and was buried in the tray
        # menu; without this button the sequence is copy, dismiss, click,
        # paste, for what is one action.
        self._type_button = theme.button(footer, t("result.type_here"),
                                         self._retype, primary=True)
        self._type_button.pack(side="right")
        self._copy_button = theme.button(footer, t("result.copy"), self._copy)
        self._copy_button.pack(side="right", padx=(0, theme.px(10)))
        # No "Saved to History" here: the header above already says it, as
        # part of the reason ("Couldn't type it, saved to History"). Twice, in
        # two different wordings, read as two different facts.

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
        self._copy_button.configure(text=t("result.copy"), bg=theme.ACCENT)

        self._place()
        self._window.deiconify()
        winapi.raise_without_focus(self._window)

        if self._after_id:
            self._window.after_cancel(self._after_id)
        self._after_id = self._window.after(AUTO_HIDE_MS, self._hide)

        # Twenty seconds is fine for a panel nobody is looking at, and rude to
        # one somebody is reading: the transcript can be much longer than the
        # four lines shown. The pointer is the only signal available here (the
        # window is deliberately non-activating, so there is no focus to
        # watch), and it is a good one.
        def hold(_event=None) -> None:
            if self._after_id:
                self._window.after_cancel(self._after_id)
                self._after_id = None

        def resume(_event=None) -> None:
            if self._after_id is None and self._window.winfo_viewable():
                self._after_id = self._window.after(6000, self._hide)

        self._window.bind("<Enter>", hold, add="+")
        self._window.bind("<Leave>", resume, add="+")

    def _hide(self) -> None:
        if self._window is None:
            return
        if self._after_id:
            self._window.after_cancel(self._after_id)
            self._after_id = None
        self._window.withdraw()

    def _retype(self) -> None:
        """Type the transcript into whatever is in front now.

        The panel is already sitting over the window the user meant,
        which is the whole reason this is worth a button: it hides
        itself first so the text lands in the window behind it, not in
        the panel."""
        app = self._app
        self._hide()
        if app is not None:
            app.retype_last()

    def _copy(self) -> None:
        if injector.set_clipboard_text(self._content):
            self._copy_button.configure(text=t("result.copied"), bg=theme.OK)
        else:
            self._copy_button.configure(text=t("result.failed"), bg=theme.ERROR)
            logger.warning("could not put the transcript on the clipboard")


panel = ResultPanel()
