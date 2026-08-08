"""User settings.

macOS used `defaults write com.fortunevoice.app FVModel …`. Windows has no
equivalent worth emulating (the registry is hostile to hand-editing), so the
same keys live in a JSON file the user can open in any editor:

    %APPDATA%\\FortuneVoice\\config.json

Key names are kept identical to the macOS `FV*` defaults so the two ports'
documentation, metrics and bug reports stay comparable.

Reads are cached with an mtime check: the dictation hot path touches config
several times per keypress, and re-parsing the file each time is pointless —
but a user editing the file mid-session should still see it take effect
without a restart.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    # Hold this to dictate. Modifiers: ctrl, alt, shift, win. See hotkey.py for
    # the accepted key names. NOT alt+space like macOS: on Windows that is the
    # system window menu, and swallowing it globally breaks a real OS shortcut.
    "FVHotkey": "ctrl+alt+space",
    # "hold" (push-to-talk) or "toggle" (tap to start, tap to stop).
    "FVActivationMode": "hold",
    # Whisper weights. Prefix-matched names from the macOS build don't apply:
    # faster-whisper takes a CTranslate2 repo id or a local path.
    "FVModel": "large-v3-turbo",
    # Fallback when FVModel won't load (no VRAM, bad download).
    "FVFallbackModel": "small",
    # "cuda", "cpu", or "auto" (cuda when available, else cpu).
    "FVDevice": "auto",
    # "ru", "en", … or "auto" to detect per dictation.
    "FVLanguage": "ru",
    # Language of the app's own windows and menus: "ru", "en", or "auto"
    # (follow Windows). Deliberately separate from FVLanguage — the language
    # you dictate in has nothing to do with the language you want the buttons
    # in, and dictating English notes should not flip the UI.
    "FVUILanguage": "auto",
    # LLM cleanup through a local Ollama.
    "FVCleanupEnabled": True,
    # Repair garbled (low-confidence) transcripts even when cleanup is off.
    "FVSmartFix": True,
    # Measured on a 6 GB card with large-v3-turbo already resident, four Russian
    # samples each: qwen2.5:3b cleaned all four in 546-657 ms. gemma3:4b was
    # cleaner still but 1141 ms and prefixed its answer with a bullet.
    # qwen2.5:1.5b answered in ~330 ms and TRANSLATED all four — into English
    # three times and Chinese once — so every one was thrown away by the safety
    # check and the user silently got no cleanup at all. Small is not free.
    "FVOllamaModel": "qwen2.5:3b",
    "FVOllamaHost": "http://localhost:11434",
    # Use the stripped-down prompt for short dictations. Upstream's default,
    # and correct when prompt-eval dominates the round-trip. Set false to send
    # the full taxonomy every time: on a model that follows it and answers in
    # ~300 ms, the mini prompt only loses accuracy. See PORT_NOTES.md.
    "FVMiniPrompt": True,
    # Transcribe while the user is still talking.
    "FVStreaming": True,
    # Paste the stitched streaming result (vs. a full batch re-decode).
    "FVStreamingV2": True,
    # Compute both and paste the batch one, logging the diff. Costs a second
    # full decode per dictation — for validating a change, not for daily use.
    "FVStreamingShadow": False,
    # Route text through the clipboard + Ctrl+V instead of typing it. Escape
    # hatch for apps that ignore synthesized unicode input.
    "FVPasteViaClipboard": False,
    "FVDebugTimings": False,
    # Days of dictation history to keep. 0 = forever.
    "FVRetentionDays": 0,
    # Input device name substring; empty = system default.
    "FVMicrophone": "",
    # Play a sound on start/success/error.
    "FVSounds": True,
    # Floating pill showing recording/transcribing state over other windows.
    "FVOverlay": True,
    # Set once the first-run screen has been dismissed.
    "FVOnboarded": False,
    # Last size and position of the main window, "WxH+X+Y". Empty = centre
    # it at the default size.
    "FVWindowGeometry": "",
}

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_mtime: float | None = None


def _load() -> dict[str, Any]:
    global _cache, _cache_mtime
    path = paths.config_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    with _lock:
        if mtime == _cache_mtime and _cache:
            return _cache
        data: dict[str, Any] = {}
        if mtime is not None:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt config must never stop the app from dictating.
                data = {}
        merged = dict(DEFAULTS)
        if isinstance(data, dict):
            merged.update(data)
        _cache = merged
        _cache_mtime = mtime
        return merged


def get(key: str, default: Any = None) -> Any:
    return _load().get(key, DEFAULTS.get(key, default))


def get_bool(key: str) -> bool:
    return bool(get(key))


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, 0))


def get_str(key: str) -> str:
    value = get(key)
    return "" if value is None else str(value)


def set(key: str, value: Any) -> None:  # noqa: A001 - mirrors UserDefaults.set
    global _cache_mtime
    path = paths.config_file()
    current = dict(_load())
    current[key] = value
    # Only persist what differs from the defaults, so a future default change
    # reaches users who never touched that setting.
    stored = {k: v for k, v in current.items() if DEFAULTS.get(k) != v}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stored, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _cache_mtime = None  # force a reload on the next read


def toggle(key: str) -> bool:
    value = not get_bool(key)
    set(key, value)
    return value
