"""`python -m fortunevoice doctor`: does this machine actually work?

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
        _line(FAIL, "microphone", "no input devices found; check Windows sound settings")
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
        _line(FAIL, "capture", f"only {samples.size} samples in 1 s: the device is not producing audio")
        return False
    level = audio.max_window_rms(samples)
    if level < 0.001:
        _line(WARN, f"capture ({samples.size} samples, peak RMS {level:.4f})",
              "silent: that's expected if you weren't speaking, a problem if you were")
    else:
        _line(OK, f"capture ({samples.size} samples, peak RMS {level:.4f})")
    return True


def _check_model() -> bool:
    from . import winapi
    from .transcriber import Transcriber, TranscriberError

    # The app holds the model on the GPU, and a second process asking
    # for it gets an out-of-memory error that has nothing to do with the
    # machine being wrong. Said before the attempt, so the failure below
    # reads as "of course" rather than "your setup is broken".
    if not winapi.claim_single_instance():
        _line(
            WARN,
            "FortuneVoice is already running",
            "it is holding the model, so the check below competes with "
            "it for video memory. Quit from the tray and run doctor "
            "again for a true answer.",
        )

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
    wanted = config.get_str("FVModel")
    if wanted and transcriber.loaded_model != wanted:
        # The ladder degrades on purpose: a dictation with the fallback
        # beats no dictation, but silently accepting it here means the
        # check that exists to say "your setup is wrong" says nothing.
        # Seen live: the app was already holding the GPU, so a second
        # process could not allocate and doctor reported a tick.
        _line(
            WARN,
            f"{wanted} did not load, running on {transcriber.loaded_model}",
            "usually the video memory is already taken: FortuneVoice "
            "itself, another model, or a game. Quit them and run doctor "
            "again.",
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

    from . import ollama as ollama_app

    host = config.get_str("FVOllamaHost").rstrip("/")
    wanted = config.get_str("FVOllamaModel")
    # Start it, exactly as a dictation would. Reporting "unreachable" for a
    # server the app brings up by itself would describe a state the user never
    # actually dictates in.
    ollama_app.ensure_running()
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        binary = ollama_app.executable()
        if binary is None:
            fix = f"install Ollama from https://ollama.com, then `ollama pull {wanted}`"
        elif not config.get_bool("FVAutoStartOllama"):
            fix = (f"Ollama is installed at {binary}, but FVAutoStartOllama is off: "
                   "start it yourself, or set that setting back to true")
        else:
            fix = (f"Ollama is installed at {binary} but did not come up. "
                   "Start it by hand and see what it reports")
        _line(
            WARN,
            f"ollama at {host} unreachable ({exc})",
            f"dictation still works: you get raw Whisper text without the "
            f"cleanup pass. To enable it: {fix}",
        )
        return True

    installed = [m.get("name", "") for m in payload.get("models", [])]
    # Exact, give or take the implicit ":latest" Ollama adds. Matching on the
    # family name alone reported "qwen2.5:3b available" when what was installed
    # was qwen2.5:1.5b, a different model, which cleanup then asked for and
    # got a 404 from, falling back to raw text with doctor still showing a tick.
    def _same(name: str) -> bool:
        head, _, tag = name.partition(":")
        want_head, _, want_tag = wanted.partition(":")
        return head == want_head and (tag or "latest") == (want_tag or "latest")

    if any(_same(name) for name in installed):
        _line(OK, f"ollama {wanted} available")
    else:
        _line(
            WARN,
            f"ollama is running but {wanted} is not installed",
            f"run: ollama pull {wanted}   (installed: {', '.join(installed) or 'none'})",
        )
    return True


def _check_vram() -> bool:
    """Can the GPU hold Whisper and the cleanup model at the same time?

    Measured on a 6 GB RTX 3060 Laptop: large-v3-turbo at float16 plus its CUDA
    context takes ~3.8 GB, and a resident qwen2.5:3b takes another ~2.1 GB.
    Together that is 5.9 GB of 6, leaving 72 MB free, and the decode of a 12 s
    clip went from 563 ms (21x realtime) to 5766 ms (2.1x). A ten-fold
    slowdown, with nothing on screen to explain it.

    The app asks Ollama to keep the cleanup model loaded for 24h, which is
    right on Apple silicon (unified memory, plenty of it) and wrong on a small
    discrete card. This check exists so that trade is visible instead of being
    a silent tax on every dictation.
    """
    if not (config.get_bool("FVCleanupEnabled") or config.get_bool("FVSmartFix")):
        return True  # nothing will be loaded beside Whisper
    free, total = _gpu_memory()
    if total is None:
        return True  # no NVIDIA GPU, or nvidia-smi missing: nothing to warn about
    if total >= 8 * 1024:
        _line(OK, f"GPU memory {total // 1024} GB, room for both models")
        return True
    if free is not None and free < 512:
        _line(
            WARN,
            f"GPU memory nearly full ({free} MiB free of {total} MiB)",
            "Whisper and the Ollama cleanup model are competing for the same card. "
            "Measured cost: decodes run up to 10x slower. Either turn cleanup off "
            '("FVCleanupEnabled": false, "FVSmartFix": false), pick a smaller '
            'Whisper model, or set "FVDevice": "cpu" for the cleanup model\'s sake.',
        )
        return True
    _line(
        WARN,
        f"GPU has {total // 1024} GB, tight for Whisper plus a cleanup model",
        "If dictations feel slow, check `nvidia-smi`: with both models resident "
        "there may be no headroom left, which costs far more than cleanup saves.",
    )
    return True


def _gpu_memory() -> tuple[int | None, int | None]:
    """(free, total) MiB from nvidia-smi, or (None, None)."""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""
    try:
        free, total = (int(part.strip()) for part in line.split(","))
    except ValueError:
        return None, None
    return free, total


def _check_injection() -> bool:
    from . import injector

    editable = injector.focused_element_is_editable()
    _line(
        OK,
        "text injection via SendInput",
        f"focused element reports editable={editable} "
        "(None just means Windows can't say; FortuneVoice types anyway)",
    )
    return True


def run() -> int:
    print(f"FortuneVoice doctor, data in {paths.home()}\n")
    checks = [
        _check_hotkey,
        _check_audio,
        _check_injection,
        _check_model,
        _check_ollama,
        _check_vram,
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
        print(f"{failed} check(s) failed: fix those before expecting dictation to work.")
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
