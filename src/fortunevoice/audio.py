"""Microphone capture as 16 kHz mono float32 — Whisper's native input format.

Port of Sources/FortuneVoice/AudioRecorder.swift. AVAudioEngine becomes
PortAudio (via sounddevice); the accumulate-under-a-lock structure, the
capacity reservation and the "a straggler callback must never write into the
next session" guard are carried over unchanged, because they are what make a
recording either whole or honestly truncated — never silently half-empty.

Sample rate: we ask the device for 16 kHz directly. Windows' shared-mode audio
engine resamples for us on essentially every modern machine. When a device
refuses (some exclusive-mode setups, odd USB interfaces), we fall back to its
native rate and decimate here rather than failing the dictation.
"""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from .log import get as get_logger

logger = get_logger("audio")

SAMPLE_RATE = 16_000
BLOCK_SIZE = 1024
# 5 minutes without a reallocation, matching the macOS reservation and the
# hard recording cap in app.py.
_INITIAL_CAPACITY = SAMPLE_RATE * 300


class AudioError(RuntimeError):
    pass


def _sounddevice():
    try:
        import sounddevice  # noqa: PLC0415 - imported lazily so tests don't need PortAudio
    except (ImportError, OSError) as exc:
        raise AudioError(f"audio backend unavailable: {exc}") from exc
    return sounddevice


def input_devices() -> list[tuple[int, str]]:
    """(index, name) for every input-capable device."""
    try:
        sd = _sounddevice()
        return [
            (i, d["name"])
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
    except Exception as exc:  # noqa: BLE001 - a broken enumeration is not fatal
        logger.warning("could not list input devices: %s", exc)
        return []


def resolve_device(name_fragment: str) -> int | None:
    """Device index whose name contains `name_fragment` (case-insensitive), or
    None for the system default."""
    if not name_fragment:
        return None
    fragment = name_fragment.lower()
    for index, name in input_devices():
        if fragment in name.lower():
            return index
    logger.warning("microphone %r not found, using the system default", name_fragment)
    return None


def max_window_rms(samples: np.ndarray, window: int = 8_000) -> float:
    """Loudest 0.5 s window RMS — a reliable speech-vs-silence signal, unlike
    Whisper's noSpeechProb which spikes on quiet real speech. Speech windows
    are ~0.02+; room silence is < 0.005. Windows overlap 50% so a short burst
    is never split across two of them."""
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return 0.0
    if data.size < window:
        return float(np.sqrt(np.mean(np.square(data))))
    step = window // 2
    starts = range(0, data.size - window + 1, step)
    return float(max(np.sqrt(np.mean(np.square(data[s : s + window]))) for s in starts))


def window_rms_range(samples: np.ndarray, window: int = 1_600) -> tuple[float, float] | None:
    """Quietest and loudest `window`-sample RMS (0.1 s at 16 kHz)."""
    data = np.asarray(samples, dtype=np.float32)
    count = data.size // window
    if count == 0:
        return None
    trimmed = data[: count * window].reshape(count, window)
    rms = np.sqrt(np.mean(np.square(trimmed), axis=1))
    return float(rms.min()), float(rms.max())


def prewarm() -> None:
    """Open and immediately close an input stream, so PortAudio's
    initialisation is paid before the first dictation instead of during it.

    Measured here: the first `start()` in a process takes 375 ms before a
    single sample arrives, against 78-94 ms on every one after it. Those extra
    300 ms are the beginning of whatever the user said — a whole word, lost on
    the first dictation after launch, which is the one that decides whether
    they think this works.

    Best-effort and silent: a machine with no microphone must not fail its
    startup over a warm-up.
    """
    try:
        sd = _sounddevice()
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                dtype="float32", blocksize=BLOCK_SIZE)
        stream.start()
        stream.stop()
        stream.close()
        logger.debug("audio backend warmed")
    except Exception as exc:  # noqa: BLE001 - warming is never worth failing over
        logger.debug("audio warm-up skipped: %s", exc)


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer = np.zeros(_INITIAL_CAPACITY, dtype=np.float32)
        self._count = 0
        self._recording = False
        self._stream = None
        self._source_rate = SAMPLE_RATE
        self._resample_tail = np.zeros(0, dtype=np.float32)
        # Called from the audio thread with the block's RMS (0…~1).
        self.on_level: Callable[[float], None] | None = None
        # Called when the input device genuinely can't continue.
        self.on_interrupted: Callable[[], None] | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(self, device_name: str = "") -> None:
        with self._lock:
            if self._recording:
                return
            self._count = 0
            self._resample_tail = np.zeros(0, dtype=np.float32)

        sd = _sounddevice()
        device = resolve_device(device_name)
        rate, stream = self._open(sd, device)
        self._source_rate = rate
        self._stream = stream
        with self._lock:
            self._recording = True
        try:
            stream.start()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._recording = False
            self._close_stream()
            raise AudioError(f"could not start the microphone: {exc}") from exc
        logger.info("recording at %d Hz (device %s)", rate, device if device is not None else "default")

    def _open(self, sd, device: int | None):
        """Prefer a native 16 kHz stream; fall back to the device's own rate."""
        try:
            return SAMPLE_RATE, sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                device=device,
                callback=self._callback,
                finished_callback=self._finished,
            )
        except Exception as exc:  # noqa: BLE001 - device refused 16 kHz
            logger.info("device refused 16 kHz (%s), using its native rate", exc)

        try:
            info = sd.query_devices(device if device is not None else sd.default.device[0])
            native = int(info["default_samplerate"])
        except Exception as exc:  # noqa: BLE001
            raise AudioError(f"no usable audio input device: {exc}") from exc

        try:
            return native, sd.InputStream(
                samplerate=native,
                channels=1,
                dtype="float32",
                blocksize=0,
                device=device,
                callback=self._callback,
                finished_callback=self._finished,
            )
        except Exception as exc:  # noqa: BLE001
            raise AudioError(f"no usable audio input device: {exc}") from exc

    def _finished(self) -> None:
        """PortAudio closed the stream on its own — device unplugged or switched
        away. Don't throw away what was already captured: the app transcribes
        and delivers it, exactly like a normal key-up. Truncated beats erased.
        """
        if self.is_recording and self.on_interrupted:
            logger.warning("audio stream ended mid-recording — salvaging")
            self.on_interrupted()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            # Overflows are routine under load and cost a few ms of audio; they
            # are not a reason to abandon the dictation.
            logger.debug("audio status: %s", status)
        block = np.asarray(indata, dtype=np.float32).reshape(-1)
        if self._source_rate != SAMPLE_RATE:
            block = self._downsample(block)
        if block.size == 0:
            return

        with self._lock:
            if not self._recording:
                return  # session already stopped; a straggler must not write
            needed = self._count + block.size
            if needed > self._buffer.size:
                grown = np.zeros(max(needed, self._buffer.size * 2), dtype=np.float32)
                grown[: self._count] = self._buffer[: self._count]
                self._buffer = grown
            self._buffer[self._count : needed] = block
            self._count = needed

        if self.on_level:
            self.on_level(float(np.sqrt(np.mean(np.square(block)))))

    def _downsample(self, block: np.ndarray) -> np.ndarray:
        """Native rate → 16 kHz.

        A box filter over the source window before picking each output sample:
        it is a crude low-pass, but it is the anti-aliasing that matters here,
        and Whisper's own front end throws away everything above 8 kHz anyway.
        The tail carries the remainder across callbacks so no sample is dropped
        at a block boundary.
        """
        if self._resample_tail.size:
            block = np.concatenate((self._resample_tail, block))
        ratio = self._source_rate / SAMPLE_RATE
        out_count = int(block.size / ratio)
        if out_count <= 0:
            # Copied. PortAudio hands the callback a buffer it reuses for the
            # next block, so holding the array itself means the tail is
            # overwritten with newer audio before it is ever consumed — a
            # torn sample at the seam, on exactly the small-block devices
            # this branch exists for.
            self._resample_tail = block.copy()
            return np.zeros(0, dtype=np.float32)
        consumed = int(out_count * ratio)
        self._resample_tail = block[consumed:].copy()

        width = max(1, int(ratio))
        indices = (np.arange(out_count) * ratio).astype(np.int64)
        if width == 1:
            return block[indices].astype(np.float32, copy=False)
        offsets = np.arange(width)
        windows = np.clip(indices[:, None] + offsets[None, :], 0, block.size - 1)
        return block[windows].mean(axis=1).astype(np.float32, copy=False)

    def snapshot(self) -> np.ndarray:
        """Thread-safe copy of everything captured so far — feeds the streaming
        transcriber while the recording continues."""
        with self._lock:
            return self._buffer[: self._count].copy()

    def stop(self) -> np.ndarray:
        """Stop recording and return the captured 16 kHz mono samples."""
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            self._recording = False  # straggler callbacks bail from here on
        self._close_stream()
        with self._lock:
            samples = self._buffer[: self._count].copy()
            # Free the ~19 MB reservation instead of keeping it — this is a
            # tray app that idles for hours between dictations.
            self._buffer = np.zeros(_INITIAL_CAPACITY, dtype=np.float32)
            self._count = 0
        return samples

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001 - closing must never raise upward
            logger.warning("closing the audio stream failed: %s", exc)
