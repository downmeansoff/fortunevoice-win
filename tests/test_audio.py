"""Microphone capture.

No microphone is touched: the tests call the audio-thread callback directly
with blocks of samples, which is exactly what PortAudio does. That covers the
parts that decide whether a recording comes out whole — accumulation, buffer
growth, the straggler guard, and the resampler's tail carry.
"""

from __future__ import annotations

import numpy as np
import pytest

from fortunevoice import audio


def block(count: int, value: float = 0.5) -> np.ndarray:
    return np.full(count, value, dtype=np.float32)


def feed(recorder: audio.AudioRecorder, samples: np.ndarray) -> None:
    """One audio-thread callback, the way PortAudio delivers it."""
    recorder._callback(samples.reshape(-1, 1), samples.size, None, None)


@pytest.fixture
def recording():
    recorder = audio.AudioRecorder()
    recorder._recording = True
    recorder._source_rate = audio.SAMPLE_RATE
    return recorder


# ── accumulation ─────────────────────────────────────────────────────────


def test_blocks_accumulate_in_order(recording):
    feed(recording, block(100, 0.1))
    feed(recording, block(100, 0.2))
    captured = recording.snapshot()
    assert captured.size == 200
    assert captured[0] == pytest.approx(0.1)
    assert captured[150] == pytest.approx(0.2)


def test_the_buffer_grows_past_its_reservation(recording):
    """Five minutes is reserved; the cap in app.py is the only real limit, and
    a long dictation must not be silently truncated at the reservation."""
    recording._buffer = np.zeros(1000, dtype=np.float32)
    for _ in range(5):
        feed(recording, block(400))
    assert recording.snapshot().size == 2000


def test_a_straggler_callback_cannot_write_into_the_next_recording(recording):
    """PortAudio can deliver one more block after stop(). Landing in the next
    session's buffer would prepend a stranger's audio to a fresh dictation."""
    feed(recording, block(100, 0.3))
    recording._recording = False          # what stop() does first
    feed(recording, block(100, 0.9))      # the straggler
    assert recording.snapshot().size == 100


def test_snapshot_is_a_copy(recording):
    """It feeds the streaming transcriber while capture continues; handing out
    the live buffer would let a decode read half-written samples."""
    feed(recording, block(50, 0.4))
    taken = recording.snapshot()
    feed(recording, block(50, 0.8))
    assert taken.size == 50
    assert taken[0] == pytest.approx(0.4)


def test_levels_are_reported_to_the_meter(recording):
    """A dead microphone must not look like a working one — the tray bars and
    the overlay waveform are driven from here."""
    levels: list[float] = []
    recording.on_level = levels.append
    feed(recording, block(100, 0.5))
    assert levels and levels[0] == pytest.approx(0.5, abs=1e-3)


# ── resampling, for devices that refuse 16 kHz ───────────────────────────


def test_downsampling_keeps_the_duration(recording):
    """48 kHz in, 16 kHz out: one second must stay one second."""
    recording._source_rate = 48_000
    feed(recording, block(48_000))
    assert recording.snapshot().size == pytest.approx(16_000, abs=2)


def test_the_resampler_carries_its_remainder_across_blocks(recording):
    """A block whose length is not a whole multiple of the ratio leaves a
    remainder. Dropping it would lose samples at every block boundary — a
    steady, quiet erosion of the recording."""
    recording._source_rate = 48_000
    for _ in range(100):
        feed(recording, block(1000))      # 1000/3 is not a whole number
    assert recording.snapshot().size == pytest.approx(100_000 / 3, abs=3)


def test_a_block_shorter_than_the_ratio_is_held_not_dropped(recording):
    recording._source_rate = 48_000
    feed(recording, block(2))             # too short to yield one output sample
    assert recording.snapshot().size == 0
    assert recording._resample_tail.size == 2, "held for the next callback"


# ── loudness helper, used by the silence guard ───────────────────────────


def test_max_window_rms_finds_the_loudest_stretch():
    """This is the silence guard's evidence: a dictation that is mostly quiet
    but contains real speech must not be thrown away as silence."""
    samples = np.concatenate([np.zeros(40_000, dtype=np.float32),
                              np.full(8_000, 0.5, dtype=np.float32),
                              np.zeros(40_000, dtype=np.float32)])
    assert audio.max_window_rms(samples) == pytest.approx(0.5, abs=0.01)


def test_max_window_rms_of_true_silence_is_near_zero():
    assert audio.max_window_rms(np.zeros(32_000, dtype=np.float32)) == pytest.approx(0.0)


def test_max_window_rms_handles_audio_shorter_than_a_window():
    """A 0.2 s dictation is shorter than the 0.5 s window; it still has to be
    measured rather than divided by zero."""
    assert audio.max_window_rms(np.full(3_200, 0.4, dtype=np.float32)) == pytest.approx(0.4)
    assert audio.max_window_rms(np.zeros(0, dtype=np.float32)) == 0.0


# ── warm-up ──────────────────────────────────────────────────────────────


def test_prewarm_survives_a_machine_with_no_audio(monkeypatch):
    """It runs on a startup thread. A box with no microphone must still start."""
    def no_backend():
        raise audio.AudioError("no PortAudio here")

    monkeypatch.setattr(audio, "_sounddevice", no_backend)
    audio.prewarm()  # must not raise


def test_stop_releases_the_reservation():
    """A tray app idles for hours between dictations; holding ~19 MB of zeroes
    the whole time is the kind of thing nobody notices and everybody pays."""
    recorder = audio.AudioRecorder()
    recorder._recording = True
    feed(recorder, block(1000))
    recorder.stop()
    assert recorder._count == 0
    assert recorder.snapshot().size == 0
