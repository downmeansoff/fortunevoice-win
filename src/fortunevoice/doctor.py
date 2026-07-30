"""`python -m fortunevoice doctor` — does this machine actually work?

A tray app fails invisibly. Every "it just doesn't type anything" report comes
down to one of five things: no microphone, a CUDA runtime that won't load, a
model that never downloaded, Ollama not running, or a hotkey string with a
typo. This checks all five and prints what to do about each, so the answer
arrives before the bug report does.
"""

from __future__ import annotations

import sys
import time

from . import config, metrics, paths

OK = "  ok   "
WARN = " warn  "
FAIL = " FAIL  "


def _line(status: str, title: str, detail: str = "") -> None:
    print(f"[{status}] {title}" + (f"\n         {detail}" if detail else ""))


def _check_hotkey() -> bool:
    from .hotkey import parse

    spec = config.get_str("FVHotkey")
    try:
        parsed = parse(spec)
    except ValueError as exc:
        _line(FAIL, f"hotkey {spec!r}", f"{exc}. Fix FVHotkey in {paths.config_file()}")
        return False
    _line(OK, f"hotkey {parsed.label}")
    return True


def _check_audio() -> bool:
    from . import audio

    try:
        devices = audio.input_devices()
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "microphone", f"could not enumerate devices: {exc}")
        return False
    if not devices:
        _line(FAIL, "microphone", "no input devices found — check Windows sound settings")
        return False

    wanted = config.get_str("FVMicrophone")
    chosen = audio.resolve_device(wanted)
    names = ", ".join(name for _i, name in devices[:4])
    _line(OK, f"microphone ({len(devices)} input devices)", names)
    if wanted and chosen is None:
        _line(WARN, f"FVMicrophone {wanted!r} not found", "falling back to the system default")

    # A real capture: enumeration succeeding says nothing about a device that
    # is present but exclusively held by another app.
    recorder = audio.AudioRecorder()
    try:
        recorder.start(wanted)
        time.sleep(1.0)
        samples = recorder.stop()
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "capture", f"could not record: {exc}")
        return False
    if samples.size < 8_000:
        _line(FAIL, "capture", f"only {samples.size} samples in 1 s — the device is not producing audio")
        return False
    level = audio.max_window_rms(samples)
    if level < 0.001:
        _line(WARN, f"capture ({samples.size} samples, peak RMS {level:.4f})",
              "silent — that's expected if you weren't speaking, a problem if you were")
    else:
        _line(OK, f"capture ({samples.size} samples, peak RMS {level:.4f})")
    return True


def _check_model() -> bool:
    from .transcriber import Transcriber, TranscriberError

    transcriber = Transcriber()
    started = time.monotonic()
    try:
        transcriber.load()
    except TranscriberError as exc:
        _line(FAIL, "whisper model", str(exc))
        return False
    _line(
        OK,
        f"whisper model {transcriber.loaded_model} on {transcriber.device}/{transcriber.compute_type}",
        f"loaded in {time.monotonic() - started:.1f}s from {paths.models_dir()}",
    )
    if transcriber.device == "cpu":
        _line(
            WARN,
            "running on the CPU",
            "expect several seconds per dictation. Install the CUDA libraries: "
            "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12",
        )

    import numpy as np

    started = time.monotonic()
    try:
        transcriber.transcribe(np.zeros(16_000, dtype=np.float32))
    except Exception as exc:  # noqa: BLE001
        _line(FAIL, "decode", f"a 1 s decode failed: {exc}")
        return False
    _line(OK, f"decode of 1 s of silence in {time.monotonic() - started:.1f}s")
    return True


def _check_ollama() -> bool:
    if not (config.get_bool("FVCleanupEnabled") or config.get_bool("FVSmartFix")):
        _line(OK, "ollama cleanup disabled")
        return True

    import json
    import urllib.error
    import urllib.request

    host = config.get_str("FVOllamaHost").rstrip("/")
    wanted = config.get_str("FVOllamaModel")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _line(
            WARN,
            f"ollama at {host} unreachable ({exc})",
            "dictation still works — you get raw Whisper text without the cleanup pass. "
            "To enable it: install Ollama, then `ollama pull " + wanted + "`",
        )
        return True

    installed = [m.get("name", "") for m in payload.get("models", [])]
    if any(name == wanted or name.startswith(wanted.split(":")[0]) for name in installed):
        _line(OK, f"ollama {wanted} available")
    else:
        _line(
            WARN,
            f"ollama is running but {wanted} is not installed",
            f"run: ollama pull {wanted}   (installed: {', '.join(installed) or 'none'})",
        )
    return True


def _check_injection() -> bool:
    from . import injector

    editable = injector.focused_element_is_editable()
    _line(
        OK,
        "text injection via SendInput",
        f"focused element reports editable={editable} "
        "(None just means Windows can't say — FortuneVoice types anyway)",
    )
    return True


def run() -> int:
    print(f"FortuneVoice doctor — data in {paths.home()}\n")
    checks = [
        _check_hotkey,
        _check_audio,
        _check_injection,
        _check_model,
        _check_ollama,
    ]
    failed = 0
    for check in checks:
        try:
            if not check():
                failed += 1
        except Exception as exc:  # noqa: BLE001 - one broken check shouldn't hide the rest
            _line(FAIL, check.__name__, repr(exc))
            failed += 1
    print()
    if failed:
        print(f"{failed} check(s) failed — fix those before expecting dictation to work.")
        return 1
    print("All checks passed.")
    return 0


def stats() -> int:
    rows = metrics.read_all()
    if not rows:
        print("No dictations recorded yet.")
        return 0

    def median(values: list[float]) -> float:
        values = sorted(values)
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    typed = [r for r in rows if str(r.get("outcome", "")).startswith("pasted")]
    print(f"{len(rows)} dictations, {len(typed)} typed into an app")
    print(f"  median key-up → typed : {median([r['total_ms'] for r in rows]):.0f} ms")
    print(f"  median decode         : {median([r['stt_ms'] for r in rows]):.0f} ms")
    cleanups = [r["cleanup_ms"] for r in rows if not r.get("cleanup_skipped")]
    if cleanups:
        print(f"  median cleanup        : {median(cleanups):.0f} ms  ({len(cleanups)} runs)")
    print(f"  median audio captured : {median([r['capture_sec'] for r in rows]):.1f} s")

    outcomes: dict[str, int] = {}
    for row in rows:
        outcomes[str(row.get("outcome", "?"))] = outcomes.get(str(row.get("outcome", "?")), 0) + 1
    print("\n  outcomes:")
    for name, count in sorted(outcomes.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<16} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
