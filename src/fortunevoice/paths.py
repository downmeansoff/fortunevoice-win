"""Where FortuneVoice keeps its state on Windows.

macOS put everything under ~/Library/Application Support/FortuneVoice. The
Windows equivalent is %APPDATA%\\FortuneVoice, roaming, per-user, and not
synced by OneDrive's Desktop/Documents redirection (which would push dictation
history and recovery audio into the cloud without the user asking).

FORTUNEVOICE_HOME overrides the whole tree; tests point it at a temp dir.
"""

from __future__ import annotations

import os
from pathlib import Path


def _base() -> Path:
    override = os.environ.get("FORTUNEVOICE_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "FortuneVoice"
    return Path.home() / ".fortunevoice"


def home() -> Path:
    path = _base()
    path.mkdir(parents=True, exist_ok=True)
    return path


def subdir(name: str) -> Path:
    path = home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return home() / "config.json"


def history_file() -> Path:
    return home() / "history.json"


def metrics_file() -> Path:
    return home() / "metrics.jsonl"


def dictionary_file() -> Path:
    return home() / "dictionary.json"


def recovery_dir() -> Path:
    return subdir("recovery")


def log_file() -> Path:
    return subdir("logs") / "fortunevoice.log"


def models_dir() -> Path:
    """Whisper weights. Kept inside our own tree rather than the HuggingFace
    cache so uninstalling means deleting one folder."""
    return subdir("models")
