"""The floating recording pill.

Port of Sources/FortuneVoice/RecordingPill.swift: a small dark capsule near the
bottom of the screen with a row of bars that move with the voice. Without it
the app has no visual state at all — you hold a hotkey and have to guess
whether anything is listening.

Three things make it behave rather than annoy:

* **It never takes focus.** WS_EX_NOACTIVATE, and it is raised with
  SetWindowPos(SWP_NOACTIVATE) rather than Tk's `lift()`, which activates. If
  this window ever became the foreground one, the dictation would be typed
  into it instead of into the user's editor.
* **It never blocks a click.** WS_EX_TRANSPARENT lets the mouse straight
  through, so a pill sitting over a button does not eat the press.
* **Levels are sampled, not queued.** The audio thread writes one float; the
  UI redraws on its own 30 fps timer. Queueing a job per audio block would put
  ~90 callbacks a second through the UI queue to draw the same frame.
"""

from __future__ import annotations

import time

from .. import winapi
from ..log import get as get_logger
from . import theme, ui

logger = get_logger("ui.pill")

HEIGHT = theme.px(40)
# Distance from the bottom of the work area. Clear of the taskbar, and clear of
# the place most apps put their own status bars.
BOTTOM_MARGIN = theme.px(96)

BARS = 20
BAR_W = theme.px(3)
BAR_GAP = theme.px(3)
BAR_MAX = theme.px(20)
BAR_MIN = theme.px(2)

_PAD = theme.px(16)
_LABEL_GAP = theme.px(12)
_BARS_W = BARS * BAR_W + (BARS - 1) * BAR_GAP
# Room for the longest label ("No mic signal") at 8 pt. Sized once here rather
# than measured at runtime: a pill whose width changed with its own state
# would jump around under the user's eyes.
_LABEL_W = theme.px(84)
WIDTH = _PAD + _BARS_W + _LABEL_GAP + _LABEL_W + _PAD

# Magenta is the colour key for the rounded corners: anything painted in it
# becomes a hole in the window. Nothing else may use it.
CHROMA = "#FF00FE"

_MODE_COLOUR = {
    "recording": theme.RECORDING,
    "processing": theme.PROCESSING,
    "error": theme.ERROR,
    "no-signal": theme.TEXT_FAINT,
    "cancelled": theme.TEXT_MUTED,
}
_MODE_TEXT = {
    "recording": "Listening",
    "processing": "Transcribing",
    "error": "Failed",
    "no-signal": "No mic signal",
    "cancelled": "Cancelled",
}


class Pill:
    def __init__(self) -> None:
        self._window = None
        self._canvas = None
        self._mode = "recording"
        self._visible = False
        self._level = 0.0              # written by the audio thread
        self._history = [0.0] * BARS
        self._animating = False
        self._hide_after: float | None = None
        self._phase = 0

    # ── public API (any thread) ──────────────────────────────────────────

    def show(self, mode: str) -> None:
        ui.call(lambda: self._show(mode))

    def hide(self) -> None:
        ui.call(self._hide)

    def flash_error(self) -> None:
        self.flash("error")

    def flash(self, mode: str, seconds: float = 0.9) -> None:
        """Show a state briefly, then auto-hide.

        Used for the outcomes that leave nothing on screen to explain
        themselves: a failed dictation and a cancelled one. Success needs no
        visual — the text appearing plus the sound cue say it.
        """
        def job() -> None:
            self._show(mode)
            self._hide_after = time.monotonic() + seconds
        ui.call(job)

    def push_level(self, level: float) -> None:
        """Called from the audio thread. Deliberately just a float store."""
        self._level = level

    # ── UI thread only ───────────────────────────────────────────────────

    def _build(self) -> None:
        import tkinter as tk

        window = tk.Toplevel(ui.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-transparentcolor", CHROMA)
        window.configure(bg=CHROMA)
        window.withdraw()

        canvas = tk.Canvas(
            window, width=WIDTH, height=HEIGHT, bg=CHROMA,
            highlightthickness=0, bd=0,
        )
        canvas.pack()

        self._window = window
        self._canvas = canvas
        # The window has to exist before its styles can be changed.
        window.update_idletasks()
        winapi.make_non_activating(window)
        winapi.make_click_through(window)
        self._place()

    def _place(self) -> None:
        """Centre it on the monitor the user is actually working on.

        Tk's screenwidth/screenheight describe the primary monitor, so on a
        two-screen desk the pill appeared on the wrong one — and even on a
        single screen it ignored the taskbar and could sit underneath it.
        The work area of the monitor holding the foreground window is the
        right frame of reference on both counts.
        """
        left, top, right, bottom = winapi.work_area_of_window(winapi.foreground_window())
        x = left + (right - left - WIDTH) // 2
        y = bottom - BOTTOM_MARGIN - HEIGHT
        self._window.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _show(self, mode: str) -> None:
        if self._window is None:
            self._build()
        self._mode = mode
        self._hide_after = None
        if not self._visible:
            self._history = [0.0] * BARS
            self._place()
            self._window.deiconify()
            self._visible = True
        # deiconify() alone leaves it behind a fullscreen app; this puts it on
        # top without stealing focus.
        winapi.raise_without_focus(self._window)
        if not self._animating:
            self._animating = True
            self._tick()

    def _hide(self) -> None:
        if self._hide_after is not None:
            return  # a flash owns its own dismissal; don't yank it early
        self._visible = False
        self._animating = False
        if self._window is not None:
            self._window.withdraw()

    def _tick(self) -> None:
        if not self._visible or self._window is None:
            self._animating = False
            return
        if self._hide_after is not None and time.monotonic() >= self._hide_after:
            self._hide_after = None
            self._hide()
            return
        self._draw()
        self._window.after(33, self._tick)

    # ── drawing ──────────────────────────────────────────────────────────

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        _rounded(canvas, 0, 0, WIDTH, HEIGHT, HEIGHT // 2, theme.INK)
        _rounded(canvas, 0, 0, WIDTH, HEIGHT, HEIGHT // 2, "", outline=theme.LINE)

        colour = _MODE_COLOUR.get(self._mode, theme.TEXT)
        self._advance()

        left = _PAD
        mid = HEIGHT / 2
        for i, value in enumerate(self._history):
            height = BAR_MIN + value * (BAR_MAX - BAR_MIN)
            x = left + i * (BAR_W + BAR_GAP)
            # Older samples fade out, so the row reads as motion even when the
            # speaker holds a steady tone.
            shade = colour if i > BARS * 0.45 else _dim(colour, 0.55)
            canvas.create_rectangle(
                x, mid - height / 2, x + BAR_W, mid + height / 2,
                fill=shade, outline="",
            )

        canvas.create_text(
            left + _BARS_W + _LABEL_GAP, mid,
            text=_MODE_TEXT.get(self._mode, ""),
            fill=theme.TEXT_MUTED if self._mode != "error" else theme.ERROR,
            font=theme.font(8), anchor="w",
        )

    def _advance(self) -> None:
        """Shift the history left and append the newest sample — a scrolling
        waveform. The app only has a per-block RMS, not a spectrum, and a
        scrolling level history is an honest picture of that."""
        self._phase += 1
        if self._mode == "recording":
            # Speech RMS is roughly 0.02-0.25; the scale puts normal speaking
            # around two thirds height instead of pinning the meter.
            value = min(1.0, (self._level / 0.22) ** 0.7)
        elif self._mode == "processing":
            # No audio is arriving any more, so show progress instead of a flat
            # line: a slow travelling bump.
            import math

            centre = (self._phase * 0.6) % BARS
            self._history = [
                max(0.05, 1.0 - abs(i - centre) / 3.0) if abs(i - centre) < 3 else 0.05
                for i in range(BARS)
            ]
            if math.isnan(self._history[0]):  # pragma: no cover - guard only
                self._history = [0.05] * BARS
            return
        else:
            value = 0.06
        self._history = self._history[1:] + [value]


def _rounded(canvas, x0, y0, x1, y1, r, fill, outline="") -> None:
    """A rounded rectangle. Tk's canvas has no primitive for one, and the
    usual polygon-with-smooth trick rounds the straight edges too."""
    kwargs = {"fill": fill, "outline": outline or fill}
    if not fill:
        kwargs = {"fill": "", "outline": outline}
        canvas.create_arc(x0, y0, x0 + 2 * r, y0 + 2 * r, start=90, extent=90, style="arc", **kwargs)
        canvas.create_arc(x1 - 2 * r, y0, x1, y0 + 2 * r, start=0, extent=90, style="arc", **kwargs)
        canvas.create_arc(x0, y1 - 2 * r, x0 + 2 * r, y1, start=180, extent=90, style="arc", **kwargs)
        canvas.create_arc(x1 - 2 * r, y1 - 2 * r, x1, y1, start=270, extent=90, style="arc", **kwargs)
        canvas.create_line(x0 + r, y0, x1 - r, y0, fill=outline)
        canvas.create_line(x0 + r, y1 - 1, x1 - r, y1 - 1, fill=outline)
        canvas.create_line(x0, y0 + r, x0, y1 - r, fill=outline)
        canvas.create_line(x1 - 1, y0 + r, x1 - 1, y1 - r, fill=outline)
        return
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, **kwargs)
    canvas.create_rectangle(x0, y0 + r, x1, y1 - r, **kwargs)
    canvas.create_oval(x0, y0, x0 + 2 * r, y0 + 2 * r, **kwargs)
    canvas.create_oval(x1 - 2 * r, y0, x1, y0 + 2 * r, **kwargs)
    canvas.create_oval(x0, y1 - 2 * r, x0 + 2 * r, y1, **kwargs)
    canvas.create_oval(x1 - 2 * r, y1 - 2 * r, x1, y1, **kwargs)


def _dim(hex_colour: str, factor: float) -> str:
    r = int(hex_colour[1:3], 16)
    g = int(hex_colour[3:5], 16)
    b = int(hex_colour[5:7], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


pill = Pill()
