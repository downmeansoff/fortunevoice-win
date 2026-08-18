"""Speech-to-text.

Port of Sources/FortuneVoice/Transcriber.swift with WhisperKit (CoreML/ANE)
replaced by faster-whisper (CTranslate2, CUDA). The wrapper's job is the same
on both platforms: load one model, serialise access to it, warm the pipeline
before the user needs it, and degrade instead of failing.

What is genuinely different on Windows:

* **Backend selection.** There is no ANE. We try CUDA float16, then CUDA
  int8_float16 (half the VRAM, for a 6 GB laptop card that is already holding
  a browser's compositor), then CPU int8. A machine with no working CUDA
  runtime still dictates, just slower — that is the whole point of the ladder.
* **The gate is a real lock.** WhisperKit was a class with one decoder cache;
  CTranslate2 models are likewise not safe to enter twice concurrently.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config, dictionary
from .log import get as get_logger
from .textclean import collapse_repeats, squeeze

logger = get_logger("transcriber")

SAMPLE_RATE = 16_000


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float
    no_speech_prob: float


@dataclass
class Result:
    text: str
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    segments: list[Segment] = field(default_factory=list)


_cuda_dlls_added = False
# Loaded CUDA libraries are kept here for the process lifetime. Dropping the
# handles would let the loader unload them and put us back where we started.
_cuda_handles: list = []

# What CTranslate2 delay-loads. Version-globbed so a cuBLAS 13 or cuDNN 10
# wheel keeps working without a code change.
_PRELOAD_PATTERNS = ("cublasLt64_*.dll", "cublas64_*.dll", "cudnn64_*.dll")


def _add_cuda_dll_directories() -> None:
    """Make pip-installed CUDA libraries usable by CTranslate2 on Windows.

    This is the single most common reason "CUDA doesn't work" on a Windows box
    that clearly has a working GPU. `pip install nvidia-cublas-cu12
    nvidia-cudnn-cu12` drops the DLLs under site-packages/nvidia/*/bin, and on
    Linux the loader finds them through RPATH. Windows does not, and it takes
    two separate fixes:

    1. `os.add_dll_directory` registers the folders, which is what resolves the
       libraries' own dependencies (cudnn64_9 needs cudnn_graph64_9 and
       friends).
    2. That alone is NOT enough for the ones CTranslate2 itself needs, because
       it delay-loads them: a delay-load stub calls `LoadLibraryExA(name, 0)`,
       and flags of 0 means the *standard* search order, which deliberately
       excludes directories added with AddDllDirectory. Measured on this
       machine: with only step 1, the model loads on cuda/float16 and the
       first encode dies with "Library cublas64_12.dll is not found or cannot
       be loaded".

       Loading them here by full path puts them in the process by module name,
       so the delay-load stub finds them already resident and never searches.

    Together these avoid the usual advice of hand-copying DLLs into System32.
    """
    global _cuda_dlls_added
    if _cuda_dlls_added or sys.platform != "win32":
        return
    _cuda_dlls_added = True
    try:
        import nvidia  # noqa: PLC0415
    except ImportError:
        return  # system-wide CUDA install (or none) — nothing to register

    directories: list[Path] = []
    for root in getattr(nvidia, "__path__", []):
        for candidate in sorted(Path(root).glob("*/bin")):
            if any(candidate.glob("*.dll")):
                directories.append(candidate)
                try:
                    os.add_dll_directory(str(candidate))
                except OSError as exc:
                    logger.debug("could not register %s: %s", candidate, exc)

    import ctypes  # noqa: PLC0415

    for pattern in _PRELOAD_PATTERNS:
        for directory in directories:
            for dll in sorted(directory.glob(pattern)):
                try:
                    _cuda_handles.append(ctypes.WinDLL(str(dll)))
                    logger.debug("preloaded %s", dll.name)
                except OSError as exc:
                    # A missing or mismatched library here just means we fall
                    # back down the backend ladder to the CPU.
                    logger.info("could not preload %s: %s", dll.name, exc)


class SerialGate:
    """FIFO lock with a bounded wait.

    Every wait is bounded on purpose. An unbounded version of this turned a
    wedged decode from something the timeout could abandon into a permanent
    hang: the holder never released, so every later decode — and the whole
    dictation state machine behind it — blocked forever. A caller that cannot
    get in fails fast instead, and the dictation falls back or errors the way
    it does for any other decode failure.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._busy = False
        self._queue: collections.deque[int] = collections.deque()
        self._ticket = 0

    def acquire(self, timeout: float) -> bool:
        """True when the gate was acquired; False when the wait expired — the
        caller must NOT release in that case."""
        deadline = time.monotonic() + timeout
        with self._condition:
            self._ticket += 1
            ticket = self._ticket
            self._queue.append(ticket)
            while True:
                if not self._busy and self._queue and self._queue[0] == ticket:
                    self._queue.popleft()
                    self._busy = True
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    try:
                        self._queue.remove(ticket)
                    except ValueError:
                        pass
                    self._condition.notify_all()
                    logger.warning("decoder gate wait timed out, giving up the slot")
                    return False
                self._condition.wait(remaining)

    def release(self) -> None:
        with self._condition:
            self._busy = False
            self._condition.notify_all()


class TranscriberError(RuntimeError):
    pass


class Transcriber:
    # How long a real decode may wait for the gate. Generous — a long batch
    # decode legitimately holds it for a while — but never infinite.
    GATE_WAIT_LIMIT = 180.0
    # The warmup is optional work; it must never queue behind a long decode.
    WARMUP_GATE_WAIT_LIMIT = 5.0
    # Below this idle gap the pipeline is still hot and a warmup would only
    # burn power.
    WARM_IDLE_THRESHOLD = 90.0
    # 0.5 s of near-silence. Whisper pads to its 30 s window regardless, so
    # this exercises the whole encoder + decoder path at the smallest input.
    _WARMUP_SAMPLES = np.zeros(8_000, dtype=np.float32)

    def __init__(self) -> None:
        self._model = None
        self._gate = SerialGate()
        self._lock = threading.Lock()
        self.loaded_model: str | None = None
        self.device: str | None = None
        self.compute_type: str | None = None
        self._last_decode_at: float | None = None
        self._warmup_thread: threading.Thread | None = None
        self._warmup_cancel = threading.Event()
        self._warmup_in_flight = False
        # Serialises load(). Two loaders would build two WhisperModels on the
        # same card — 2 GB each here — and the loser's would be dropped on the
        # floor after paying for it. Reachable now that a dictation reloads a
        # model the idle unload dropped: the background reload at key-down and
        # the pipeline's own load at key-up are two different threads.
        self._load_lock = threading.Lock()
        # Language auto-detect settled on for the dictation in progress.
        self._session_language: str | None = None

    # ── loading ──────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load FVModel, degrading through backends and then to the fallback
        model. Raises only when nothing at all could be loaded.

        Serialised, and a no-op when another thread already finished: paying
        twice for a 2 GB model on a 6 GB card is how both end up not fitting.
        """
        with self._load_lock:
            if self._model is not None:
                return
            self._load_locked()

    def _load_locked(self) -> None:
        from . import paths

        _add_cuda_dll_directories()
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise TranscriberError(
                "faster-whisper is not installed — run: pip install -r requirements.txt"
            ) from exc

        wanted = config.get_str("FVModel")
        fallback = config.get_str("FVFallbackModel")
        root = str(paths.models_dir())

        errors: list[str] = []
        for name in [wanted] + ([fallback] if fallback and fallback != wanted else []):
            for device, compute_type in self._backends():
                try:
                    started = time.monotonic()
                    model = WhisperModel(
                        name,
                        device=device,
                        compute_type=compute_type,
                        download_root=root,
                        # One worker: the gate already serialises us, and a
                        # second CTranslate2 worker doubles VRAM for nothing.
                        num_workers=1,
                    )
                except Exception as exc:  # noqa: BLE001 - try the next backend
                    errors.append(f"{name}/{device}/{compute_type}: {exc}")
                    logger.info("model %s on %s/%s failed: %s", name, device, compute_type, exc)
                    continue
                with self._lock:
                    self._model = model
                    self.loaded_model = name if name == wanted else f"{name} (fallback)"
                    self.device = device
                    self.compute_type = compute_type
                logger.info(
                    "loaded %s on %s/%s in %.1fs",
                    self.loaded_model, device, compute_type, time.monotonic() - started,
                )
                return
        raise TranscriberError("no model could be loaded:\n  " + "\n  ".join(errors))

    def _backends(self) -> list[tuple[str, str]]:
        """Backends to try, best first."""
        wanted = config.get_str("FVDevice").lower()
        cpu = [("cpu", "int8")]
        cuda = [("cuda", "float16"), ("cuda", "int8_float16")]
        if wanted == "cpu":
            return cpu
        if wanted == "cuda":
            return cuda + cpu  # still fall back: a dead CUDA runtime beats no dictation
        return cuda + cpu

    # ── transcription ────────────────────────────────────────────────────

    def transcribe(self, samples: np.ndarray) -> Result:
        model = self._model
        if model is None:
            raise TranscriberError("Whisper model not loaded yet")
        audio = np.asarray(samples, dtype=np.float32)
        if audio.size == 0:
            return Result(text="")

        # Forced language beats auto-detect on short utterances (detection is
        # unstable under ~3 s and mangles Russian when it guesses wrong). On
        # "auto", reuse what this dictation already detected so a streaming
        # session doesn't re-derive the same language on every pass.
        configured = config.get_str("FVLanguage")
        language = self._session_language if configured == "auto" else configured

        prompt = dictionary.prompt_string() or None

        if not self._gate.acquire(self.GATE_WAIT_LIMIT):
            raise TranscriberError("decoder busy — a previous transcription never finished")
        try:
            segments_iter, info = model.transcribe(
                audio,
                language=language,
                task="transcribe",
                # Greedy: the macOS build decoded at temperature 0, and beam
                # search costs latency the dictation loop cannot spare.
                beam_size=1,
                # Temperature fallback stays intact — it re-decodes windows that
                # fail the checks below instead of accepting truncated output.
                # Disabling it is exactly why long dictations used to lose text.
                temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                # Lower than the 2.4 default: repetition-loop hallucinations
                # produce highly compressible text, so a tighter threshold makes
                # the decoder reject and re-decode those windows.
                compression_ratio_threshold=2.0,
                # Feeding the previous window's text back is the single biggest
                # amplifier of repetition loops in faster-whisper, and dictation
                # windows are short enough not to need the extra context.
                condition_on_previous_text=False,
                # Bias the decoder toward the user's custom vocabulary.
                initial_prompt=prompt,
                word_timestamps=False,
                # The streaming session does its own RMS-based silence handling;
                # a second VAD in front of it would move the timestamps it cuts
                # on and make its confirmations wrong.
                vad_filter=False,
            )
            segments = [
                Segment(
                    start=float(s.start),
                    end=float(s.end),
                    text=s.text.strip(),
                    avg_logprob=float(s.avg_logprob),
                    no_speech_prob=float(s.no_speech_prob),
                )
                for s in segments_iter
            ]
        finally:
            self._last_decode_at = time.monotonic()
            self._gate.release()

        if configured == "auto" and getattr(info, "language", None):
            self._session_language = info.language

        joined = squeeze(" ".join(s.text for s in segments))
        text = collapse_repeats(joined)
        avg_logprob = (
            sum(s.avg_logprob for s in segments) / len(segments) if segments else 0.0
        )
        no_speech = (
            sum(s.no_speech_prob for s in segments) / len(segments) if segments else 0.0
        )
        return Result(text=text, avg_logprob=avg_logprob, no_speech_prob=no_speech,
                      segments=segments)

    def unload(self) -> bool:
        """Drop the model and give its video memory back.

        Measured here: the app holds 3180 MiB at idle against 1088 with it
        closed, so Whisper sits on ~2.1 GB of a 6 GB card while nothing is
        happening. Reloading costs 5.6 s, which is why nothing calls this
        unless the user asks for it — see FVUnloadModelAfter.

        False when there was nothing loaded, or when a decode holds the gate:
        unloading mid-transcription would take that dictation with it.
        """
        if self._model is None:
            return False
        if not self._gate.acquire(0.5):
            logger.debug("not unloading — a decode is in progress")
            return False
        try:
            with self._lock:
                self._model = None
                self.loaded_model = None
                self.device = None
                self.compute_type = None
                self._last_decode_at = None
            logger.info("model unloaded to free video memory")
        finally:
            self._gate.release()
        return True

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def reset_session_language(self) -> None:
        """Forget the detected language so the next dictation detects afresh —
        the user may well switch languages between one and the next."""
        self._session_language = None

    # ── warmup ───────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Throwaway decode that spins the pipeline back up.

        On macOS, metrics put a cold CoreML/ANE decode at ~4x a warm one, and
        that cost lands entirely inside the user's key-up → paste wait. CUDA
        has the same shape of problem: the first decode after an idle gap pays
        kernel autotuning and a cold allocator. Called on hotkey-DOWN so it is
        paid while the user is still speaking, where it is free.
        """
        with self._lock:
            if self._model is None or self._warmup_in_flight:
                return
            if (
                self._last_decode_at is not None
                and time.monotonic() - self._last_decode_at < self.WARM_IDLE_THRESHOLD
            ):
                return
            self._warmup_in_flight = True
        self._warmup_cancel.clear()
        self._warmup_thread = threading.Thread(
            target=self._warmup_run, name="whisper-warmup", daemon=True
        )
        self._warmup_thread.start()

    def _warmup_run(self) -> None:
        started = time.monotonic()
        try:
            if self._warmup_cancel.is_set():
                return
            if not self._gate.acquire(self.WARMUP_GATE_WAIT_LIMIT):
                return
            try:
                # Deliberately NOT the real decoding options: no custom
                # vocabulary, forced language, and no temperature fallback.
                # Silence can fail the compression check and send the real
                # options into repeated re-decodes, which would make the warmup
                # slower than the dictation it is meant to speed up.
                segments, _ = self._model.transcribe(
                    self._WARMUP_SAMPLES,
                    language="en",
                    beam_size=1,
                    temperature=[0.0],
                    condition_on_previous_text=False,
                    word_timestamps=False,
                    vad_filter=False,
                )
                for _ in segments:  # the generator is lazy; drain it
                    if self._warmup_cancel.is_set():
                        break
            finally:
                self._last_decode_at = time.monotonic()
                self._gate.release()
        except Exception as exc:  # noqa: BLE001 - warmup is best-effort
            logger.debug("warmup failed: %s", exc)
        finally:
            with self._lock:
                self._warmup_in_flight = False
            if config.get_bool("FVDebugTimings"):
                logger.info("whisper warmup %.0f ms", (time.monotonic() - started) * 1000)

    def cancel_warmup(self) -> None:
        """Stop any warmup so it can't sit in front of the real decode in the
        gate. Bounded: a warmup that refuses to end is left to the gate's own
        timeout rather than blocking the dictation here."""
        thread = self._warmup_thread
        if thread is None or not thread.is_alive():
            return
        self._warmup_cancel.set()
        thread.join(timeout=self.WARMUP_GATE_WAIT_LIMIT)
        self._warmup_thread = None
