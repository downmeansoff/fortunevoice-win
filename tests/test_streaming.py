"""Ported from Tests/FortuneVoiceTests/StreamingSessionTests.swift."""

import numpy as np

from fortunevoice.streaming import (
    QUIET_RMS,
    agreed_prefix_count,
    agreement_key,
    quiet_cut,
    silence_threshold,
)


def tone_silence_tone() -> np.ndarray:
    """1 s of tone, 1 s of silence, 1 s of tone @ 16 kHz."""
    tone = np.sin(np.arange(16_000, dtype=np.float32) * 0.1) * 0.3
    return np.concatenate([tone, np.zeros(16_000, dtype=np.float32), tone])


def alternating(amplitude: float, count: int) -> np.ndarray:
    """Alternating ±amplitude has RMS exactly `amplitude` — lets a test state
    the loudness it means instead of approximating it."""
    signs = np.where(np.arange(count) % 2 == 0, 1.0, -1.0)
    return (signs * amplitude).astype(np.float32)


def noisy_room_gap() -> np.ndarray:
    """Speech, a gap carrying real room noise, speech. The gap is a legitimate
    cut point but sits ABOVE the fixed 0.008 floor."""
    return np.concatenate(
        [alternating(0.3, 16_000), alternating(0.012, 16_000), alternating(0.3, 16_000)]
    )


# ── quiet_cut: the only-cut-in-silence rule ──────────────────────────────


def test_quiet_cut_true_in_silence():
    assert quiet_cut(tone_silence_tone(), 1.5)


def test_quiet_cut_false_in_speech():
    assert not quiet_cut(tone_silence_tone(), 0.5)
    assert not quiet_cut(tone_silence_tone(), 2.5)


def test_quiet_cut_false_at_silence_edge():
    # 0.05 s into the silence run the ±0.2 s window still contains tone.
    assert not quiet_cut(tone_silence_tone(), 1.05)


def test_quiet_cut_false_near_buffer_edge():
    # Too close to the end to verify ±0.2 s → must refuse the cut.
    assert not quiet_cut(np.zeros(1_000, dtype=np.float32), 0.06)


# ── adaptive silence floor ───────────────────────────────────────────────


def test_fixed_floor_misses_cut_in_noisy_room():
    # The regression this fixes: with a fan or street noise the between-
    # sentence gap never drops under the fixed floor, so nothing is ever
    # confirmed and streaming buys nothing.
    assert not quiet_cut(noisy_room_gap(), 1.5)


def test_adaptive_floor_finds_cut_in_noisy_room():
    threshold = silence_threshold(0.012)
    assert quiet_cut(noisy_room_gap(), 1.5, threshold)


def test_adaptive_floor_still_refuses_to_cut_in_speech():
    threshold = silence_threshold(0.012)
    assert not quiet_cut(noisy_room_gap(), 0.5, threshold)
    assert not quiet_cut(noisy_room_gap(), 2.5, threshold)


def test_silence_threshold_never_drops_below_the_fixed_floor():
    # A silent room must not make the threshold stricter than the default.
    assert silence_threshold(0) == QUIET_RMS
    assert silence_threshold(0.001) == QUIET_RMS


def test_silence_threshold_is_capped_below_speech():
    # A loud room must never lift the threshold into speech territory (~0.02+),
    # or the session would cut mid-sentence.
    assert silence_threshold(0.05) <= 0.015
    assert silence_threshold(10) <= 0.015


# ── double agreement ─────────────────────────────────────────────────────


def test_agreed_prefix_full_match():
    assert agreed_prefix_count(["a", "b"], ["a", "b", "c"]) == 2


def test_agreed_prefix_stops_at_first_mismatch():
    assert agreed_prefix_count(["a", "x", "c"], ["a", "b", "c"]) == 1


def test_agreed_prefix_empty():
    assert agreed_prefix_count([], ["a"]) == 0


def test_agreement_key_normalizes():
    assert agreement_key("Привет,  мир!") == agreement_key("привет мир")
