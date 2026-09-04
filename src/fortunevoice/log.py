"""Logging.

macOS had NSLog and Console.app. On Windows a tray app has no console at all
once it is launched from a shortcut, so every diagnostic goes to a rotating
file the user can hand over verbatim:

    %APPDATA%\\FortuneVoice\\logs\\fortunevoice.log

Also mirrored to stderr, which is where it lands during development.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths

_configured = False


def setup(debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("fortunevoice")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.log_file(), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        pass  # read-only profile, roaming glitch; never block startup on logging

    # `pythonw.exe` has no usable stderr; guard so a write there can't crash us.
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"fortunevoice.{name}")
