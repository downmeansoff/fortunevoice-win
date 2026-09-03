"""Audio feedback for start / success / error.

A dictation app with a hidden UI needs its state audible: the user is looking
at the app they're dictating into, not at the tray. macOS used NSSound with
system sounds; Windows has winsound in the standard library.

Every call is fire-and-forget on its own thread: winsound.Beep blocks for the
full duration, and blocking the dictation pipeline to play a 60 ms tone would
be absurd.
"""

from __future__ import annotations

import threading

from . import config
from .log import get as get_logger

logger = get_logger("sound")

# (frequency Hz, duration ms) pairs. Rising = started, short high = delivered,
# falling low = something went wrong.
_TONES = {
    "start": [(880, 60)],
    "success": [(1320, 50)],
    "error": [(400, 120), (300, 120)],
    # Cancel is not an error: two quick descending notes say "dropped, on
    # purpose", where the error pair is slower and lower.
    "cancel": [(700, 45), (520, 45)],
}


def play(name: str) -> None:
    if not config.get_bool("FVSounds"):
        return
    tones = _TONES.get(name)
    if not tones:
        return

    def run() -> None:
        try:
            import winsound  # noqa: PLC0415 - Windows-only, imported lazily

            for frequency, duration in tones:
                winsound.Beep(frequency, duration)
        except Exception as exc:  # noqa: BLE001 - audio feedback is never critical
            logger.debug("could not play %s: %s", name, exc)

    threading.Thread(target=run, name=f"sound-{name}", daemon=True).start()
