"""Entry point: `python -m fortunevoice`.

Subcommands exist for the things that are otherwise invisible in a tray app —
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


if __name__ == "__main__":
    raise SystemExit(main())
