"""Entry point: `python -m fortunevoice`.

Subcommands exist for the things that are otherwise invisible in a tray app:
checking that the machine can actually run this, and reading back the timings
the app records.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fortunevoice", description=__doc__)
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="start the tray app (default)")
    sub.add_parser("doctor", help="check audio, CUDA, model and Ollama")
    sub.add_parser("stats", help="summarise recorded dictation timings")
    args = parser.parse_args(argv)

    from . import config, log

    log.setup(debug=args.debug or config.get_bool("FVDebugTimings"))

    if args.command == "doctor":
        from .doctor import run as doctor_run

        return doctor_run()
    if args.command == "stats":
        from .doctor import stats as doctor_stats

        return doctor_stats()

    if sys.platform != "win32":
        print("FortuneVoice for Windows needs Windows.", file=sys.stderr)
        return 2

    from . import winapi

    # Before anything creates a window: a process that asks for DPI awareness
    # after the fact is ignored, and Windows bitmap-stretches the whole UI.
    winapi.set_dpi_awareness()
    # Before any window: Windows reads it once, when the first one appears.
    winapi.set_app_id()

    if not winapi.claim_single_instance():
        # Not a nicety. Each instance installs its own low-level keyboard hook,
        # so one keypress starts two recordings and the transcript is typed
        # twice into whatever the user is writing.
        _already_running()
        return 1

    from .app import App
    from .tray import Tray

    app = App()
    tray = Tray(app)
    app.start()
    try:
        tray.run()
    except KeyboardInterrupt:
        app.stop()
    return 0


def _already_running() -> None:
    """Say so visibly.

    Launched from a shortcut there is no console to print to, so a silent exit
    looks exactly like the app failing to start, and the user double-clicks
    again, and again.
    """
    import ctypes
    import logging

    logging.getLogger("fortunevoice").info("another instance is already running")
    try:
        ctypes.WinDLL("user32").MessageBoxW(
            None,
            "FortuneVoice is already running.\n\n"
            "Look for the microphone icon in the notification area, "
            "next to the clock.",
            "FortuneVoice",
            0x40,  # MB_ICONINFORMATION
        )
    except Exception:  # noqa: BLE001 - the exit code still reports it
        pass


if __name__ == "__main__":
    raise SystemExit(main())
