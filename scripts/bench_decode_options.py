"""Which decoding options make `small` usable on 2 cores.

Records 12 s once (then reuses bench-audio.npy), and decodes that same audio
under several option sets so the only thing that changes is the setting under
test. Options mirror transcriber.py:367-386.
"""

from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

AUDIO = Path(__file__).with_name("bench-audio.npy")
RESULT = Path(__file__).with_name("bench2-result.txt")
SRC = r"C:\Users\HP\dev\fortunevoice-win\src"
FULL = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

VARIANTS = [
    ("small", "baseline",     dict(temperature=FULL,  compression_ratio_threshold=2.0, vad_filter=False)),
    ("small", "no-fallback",  dict(temperature=[0.0], compression_ratio_threshold=2.0, vad_filter=False)),
    ("small", "vad",          dict(temperature=FULL,  compression_ratio_threshold=2.0, vad_filter=True)),
    ("small", "vad+nofb+2.4", dict(temperature=[0.0], compression_ratio_threshold=2.4, vad_filter=True)),
    ("base",  "vad+nofb+2.4", dict(temperature=[0.0], compression_ratio_threshold=2.4, vad_filter=True)),
]

_lines: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    _lines.append(text)


def get_audio() -> np.ndarray:
    """The saved take, so repeat runs compare options rather than readings."""
    if AUDIO.exists():
        samples = np.load(AUDIO)
        say(f"reusing {AUDIO.name}: {samples.size / 16_000:.1f} s")
        return samples

    import sounddevice as sd

    device = None
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0 and "headset microphone" in info["name"].lower():
            device = index
            say(f"microphone: [{index}] {info['name']}")
            break

    say("\nSPEAK NOW - recording 12 s. A normal working sentence.")
    for n in (3, 2, 1):
        print(f"  {n}...", flush=True)
        time.sleep(1)
    print("  GO", flush=True)

    rec = sd.rec(12 * 16_000, samplerate=16_000, channels=1, dtype="float32", device=device)
    sd.wait()
    samples = rec.reshape(-1)

    window = 8_000
    peak = 0.0
    for start in range(0, max(1, samples.size - window), window // 2):
        chunk = samples[start:start + window]
        if chunk.size:
            peak = max(peak, float(np.sqrt(np.mean(chunk ** 2))))
    say(f"captured 12.0 s, peak window RMS {peak:.4f}")
    if peak < 0.005:
        say("WARNING: that is silence. Check the microphone first.")

    np.save(AUDIO, samples)
    say(f"saved {AUDIO.name} - later runs reuse it, delete it to re-record")
    return samples


def main() -> int:
    from faster_whisper import WhisperModel

    root = None
    try:
        sys.path.insert(0, SRC)
        from fortunevoice import paths  # noqa: PLC0415
        root = str(paths.models_dir())
    except Exception:  # noqa: BLE001 - the default cache still works
        pass
    say(f"model cache: {root or 'default HuggingFace cache'}")

    try:
        audio = get_audio()
    except Exception as exc:  # noqa: BLE001 - nothing to measure without audio
        say(f"recording failed: {exc}")
        return 1
    seconds = audio.size / 16_000

    say("")
    say(f"{'model':<7}{'variant':<15}{'decode':>9}{'RTF':>7}   text")
    say("-" * 78)

    model = None
    loaded: str | None = None
    for name, label, opts in VARIANTS:
        if loaded != name:
            # One model at a time: two Whisper models resident is how a 2 GB
            # machine ends up swapping instead of decoding.
            del model
            gc.collect()
            try:
                model = WhisperModel(name, device="cpu", compute_type="int8",
                                     download_root=root, num_workers=1, cpu_threads=2)
            except Exception as exc:  # noqa: BLE001 - keep the other variants
                say(f"{name:<7}{label:<15}   FAILED to load: {exc}")
                model, loaded = None, None
                continue
            loaded = name

        try:
            started = time.monotonic()
            segments, _ = model.transcribe(
                audio, language="ru", task="transcribe", beam_size=1,
                condition_on_previous_text=False, word_timestamps=False, **opts,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            decode = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001 - report and keep going
            say(f"{name:<7}{label:<15}   FAILED: {exc}")
            continue

        say(f"{name:<7}{label:<15}{decode:>8.1f}s{decode / seconds:>7.2f}   {text}")

    say("")
    say("RTF = decode seconds per second of speech. Compare the text too:")
    say("a variant that is fast and wrong is not a win.")

    try:
        RESULT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        print(f"saved to {RESULT}", flush=True)
    except OSError as exc:
        print(f"could not write {RESULT}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
