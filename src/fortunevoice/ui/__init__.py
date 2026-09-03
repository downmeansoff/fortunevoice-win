"""The UI thread.

Two GUI event loops have to coexist in this process: pystray runs a Win32
message loop and insists on the main thread, and Tk needs a `mainloop` of its
own. So Tk gets a dedicated thread, created here, and every widget in this
package lives on it.

Tkinter is not thread-safe. The rule that makes this work is absolute: **no
code outside this module ever touches a Tk object directly.** Callers hand a
callable to `ui.call()`, it lands on a queue, and the UI thread drains it from
a timer. Breaking that rule does not raise; it corrupts the interpreter and
crashes minutes later somewhere unrelated.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

from ..log import get as get_logger

logger = get_logger("ui")

# How often the UI thread drains the queue while anything is happening. 25 ms
# is imperceptible for showing an overlay or animating the waveform.
_PUMP_MS = 25
# And how often once the queue has been empty for a while. Measured: draining
# an empty queue forty times a second costs 1.4% of a core, all day, for a tray
# app doing nothing: the single largest thing this process burns while idle.
# At 100 ms that is roughly a quarter, and the cost is at most 100 ms before
# the pill appears, which is well under the 300 ms the hotkey already spends
# deciding a modifier chord was really held.
_IDLE_PUMP_MS = 100
# Empty drains before slowing down. Two seconds of nothing at the fast rate:
# long enough that a dictation never slows mid-flight.
_IDLE_AFTER_EMPTY = 80


class UiThread:
    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._root = None
        # Consecutive drains that found nothing. See _pump.
        self._empty_drains = 0
        self._failed = False

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the thread and wait for Tk to come up. False when Tk is
        unavailable: the app must keep dictating without any of this."""
        if self._thread and self._thread.is_alive():
            return not self._failed
        self._thread = threading.Thread(target=self._run, name="ui", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        return not self._failed

    def _run(self) -> None:
        try:
            import tkinter as tk

            self._root = tk.Tk()
            # The root is never shown: it exists to own the Tk interpreter and
            # run the loop. Every real window is a Toplevel.
            self._root.withdraw()
            self._root.title("FortuneVoice")
            self._apply_scaling()
            self._apply_icon()
        except Exception as exc:  # noqa: BLE001 - a headless or broken Tk is survivable
            self._failed = True
            logger.warning("UI unavailable (%s), running without windows", exc)
            self._ready.set()
            return

        self._ready.set()
        self._root.after(_PUMP_MS, self._pump)
        try:
            self._root.mainloop()
        except Exception:  # noqa: BLE001
            logger.exception("UI loop died")

    def _apply_scaling(self) -> None:
        """Teach Tk this display's DPI.

        `tk scaling` is pixels per point. Tk assumes 96 DPI (96/72 = 1.333);
        on a 150% display the process is now DPI-aware, so points must map to
        more pixels or every label comes out two-thirds of its intended size.
        Widget dimensions are handled separately by `theme.px`.
        """
        from . import theme

        try:
            self._root.tk.call("tk", "scaling", theme.SCALE * 96 / 72)
        except Exception:  # noqa: BLE001 - cosmetic
            logger.debug("could not set Tk scaling", exc_info=True)

    def _apply_icon(self) -> None:
        from .. import assets

        try:
            self._root.iconbitmap(default=str(assets.icon_path()))
        except Exception:  # noqa: BLE001 - cosmetic only
            logger.debug("could not set the window icon", exc_info=True)

    def _pump(self) -> None:
        ran = False
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if job is None:
                self._root.quit()
                return
            ran = True
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - one bad callback must not kill the UI
                logger.exception("UI callback failed")
                self._report(exc)
        # Back to the fast rate the moment there is anything to do, so a
        # dictation's overlay and waveform are never the slow ones.
        self._empty_drains = 0 if ran else self._empty_drains + 1
        delay = (_IDLE_PUMP_MS if self._empty_drains >= _IDLE_AFTER_EMPTY
                 else _PUMP_MS)
        self._root.after(delay, self._pump)

    # Set by the app once the tray exists. A window that fails to build is
    # otherwise completely silent: the user clicks "Settings", nothing appears,
    # and the only record is a line in a log file they have no reason to open.
    # That happened twice during this port: a renamed colour constant and a
    # missing helper, and both times it was found by reading the log, not by
    # the app saying anything.
    on_error: Callable[[str], None] | None = None

    def _report(self, exc: Exception) -> None:
        if self.on_error is None:
            return
        try:
            self.on_error(f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001 - reporting must never recurse
            logger.exception("could not report a UI failure")

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)

    # ── the only safe way in ─────────────────────────────────────────────

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._failed)

    @property
    def root(self):
        """Tk root. Only valid inside a `call()` callback."""
        return self._root

    def call(self, job: Callable[[], None]) -> None:
        """Run `job` on the UI thread. Fire-and-forget; never blocks."""
        if not self.alive:
            return
        self._queue.put(job)


ui = UiThread()
