"""Incremental transcription while the user is still talking.

Port of Sources/FortuneVoice/StreamingSession.swift. This is the piece that
makes a long dictation paste in roughly the time of its last few seconds
instead of the whole utterance, so it is ported behaviour-for-behaviour —
including the two conditions that took the macOS build three attempts to get
right.

A background loop transcribes the growing audio buffer every ~1 s. Segments
become *confirmed* — their text locked in and the buffer offset advanced past
them — only when BOTH hold:

 1. **Double agreement (LocalAgreement-2):** two consecutive passes decoded the
    same text for the segment. One pass can mis-hear a boundary; two identical
    reads are stable.
 2. **Silent cut:** the audio around the segment's end is genuinely quiet (raw
    RMS, not Whisper's unreliable no_speech_prob). An earlier attempt cut at
    segment boundaries mid-speech and ate quiet endings at the seam.

At key-up, `finish` decodes only the unconfirmed tail and joins it to the
confirmed prefix.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import numpy as np

from . import config
from .audio import window_rms_range
from .cleaner import OllamaCleaner
from .log import get as get_logger
from .textclean import collapse_repeats, normalize_key, strip_special_tokens, word_diff
from .transcriber import Result, Transcriber

logger = get_logger("streaming")

SAMPLE_RATE = 16_000

# Segments must end this far before the buffer edge to be trusted — the decoder
# often revises the trailing in-progress phrase.
STABLE_MARGIN = 1.5
# Short utterances are faster as plain batch — don't burn the pipeline on them
# (a pass in flight at key-up delays the final decode).
FIRST_PASS_DELAY = 3.0
PASS_INTERVAL = 1.0
MIN_FRESH_SAMPLES = 16_000  # ≥1 s of new audio per pass
# Cap a pass to one Whisper window so segment timestamps stay simple; the tail
# is chunked at finish() if it outgrew this.
MAX_PASS_SAMPLES = 30 * SAMPLE_RATE
# RMS below this in every window around a cut point = genuine silence in a
# quiet room. silence_threshold() raises it to match a noisier one.
QUIET_RMS = 0.008
# Don't send a block below this: the ~400 ms fixed round-trip costs more than
# the cleanup saves on a handful of words.
CLEANUP_BLOCK_WORDS = 12
# At key-up, let an in-flight block cleanup land rather than throwing away work
# already paid for — but never wait out a stalled Ollama.
CLEANUP_SETTLE_SECONDS = 0.6


@dataclass
class ShadowStats:
    diff_words: int
    stitched_ms: float
    batch_ms: float


# ── pure helpers (module level so the tests don't need a model) ───────────


def agreement_key(text: str) -> str:
    """Case/punctuation-insensitive comparison key."""
    return normalize_key(text)


def agreed_prefix_count(previous: list[str], current: list[str]) -> int:
    """How many leading segments two consecutive passes agree on."""
    count = 0
    while count < len(previous) and count < len(current) and previous[count] == current[count]:
        count += 1
    return count


def silence_threshold(noise_floor: float) -> float:
    """Silence threshold for this session's actual room.

    A fixed floor is what made streaming useless in practice: with a fan or
    street noise the gaps between sentences never drop under 0.008, so no cut
    is ever taken — metrics showed 63 of 91 dictations running streaming passes
    that confirmed nothing at all. Anchoring to the quietest window actually
    observed adapts to the room, while the hard cap keeps the threshold well
    below speech (~0.02+).
    """
    return min(0.015, max(QUIET_RMS, noise_floor * 3))


def quiet_cut(samples: np.ndarray, at_seconds: float, threshold: float = QUIET_RMS) -> bool:
    """Is the ±0.2 s neighborhood of `at_seconds` genuinely quiet?

    Max RMS over 0.1 s windows stepped by 0.05 s must stay under `threshold`.
    Points too close to the buffer edges are NOT quiet — we can't verify, so we
    don't cut.
    """
    data = np.asarray(samples, dtype=np.float32)
    center = int(at_seconds * SAMPLE_RATE)
    low = max(0, center - 3_200)
    high = min(data.size, center + 3_200)
    if high - low < 1_600:
        return False
    window, step = 1_600, 800
    for start in range(low, high - window + 1, step):
        chunk = data[start : start + window]
        if float(np.sqrt(np.mean(np.square(chunk)))) >= threshold:
            return False
    return True


def max_window_rms(samples: np.ndarray, upto: int, window: int = 8_000) -> float:
    """Loudest window RMS in samples[:upto] — the long-pause detector."""
    data = np.asarray(samples, dtype=np.float32)[:upto]
    if data.size < window:
        return 0.0
    best = 0.0
    for start in range(0, data.size - window + 1, window):
        chunk = data[start : start + window]
        best = max(best, float(np.sqrt(np.mean(np.square(chunk)))))
    return best


def v2_enabled() -> bool:
    """Paste the stitched result.

    Default ON: 17 shadow runs in the macOS metrics had the stitched decode
    finish faster in 16 of them (median 1242 ms vs 1961 ms) with a content diff
    of 0–6 words in 15 of 17. The old default computed the fast result and then
    pasted the slow one.
    """
    return config.get_bool("FVStreamingV2")


def shadow_enabled() -> bool:
    """Compute both results, paste batch, log the diff. Default OFF now that V2
    is validated — leaving it on made every dictation pay for two full decodes
    and then paste the slower one."""
    return config.get_bool("FVStreamingShadow")


# ── the session ──────────────────────────────────────────────────────────


class StreamingSession:
    def __init__(
        self,
        transcriber: Transcriber,
        recorder,
        cleaner: OllamaCleaner | None = None,
        vocabulary: str = "",
    ) -> None:
        self._transcriber = transcriber
        self._recorder = recorder
        self._cleaner = cleaner
        self._vocabulary = vocabulary

        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

        # Confirmed (locked-in) state.
        self._confirmed_texts: list[str] = []
        self._confirmed_logprobs: list[float] = []
        self._confirmed_offset = 0
        self.pass_count = 0
        # Candidate segment keys from the previous pass, for the double-agreement
        # check. None after the offset moves — a new slice start makes old
        # candidates incomparable.
        self._previous_keys: list[str] | None = None
        self._noise_floor = float("inf")

        self.last_shadow: ShadowStats | None = None
        # After finish(): the confirmed prefix already cleaned during recording,
        # and the raw remainder that still needs a pass. None when no
        # incremental cleanup applies — the caller then cleans everything.
        self.last_pre_cleaned: tuple[str, str] | None = None

        # Incremental cleanup: one confirmed block at a time, in order.
        self._blocks: list[dict] = []
        self._cleaned_upto = 0
        self._cleanup_queue: queue.Queue = queue.Queue()
        self._cleanup_thread: threading.Thread | None = None
        # Blocks queued but not yet answered. Guarded by _cleanup_done, which
        # finish() waits on for a short settle.
        self._cleanup_pending = 0
        self._cleanup_done = threading.Condition()

    @property
    def debug(self) -> bool:
        return config.get_bool("FVDebugTimings")

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if not (v2_enabled() or shadow_enabled()):
            return
        self._thread = threading.Thread(target=self._loop, name="stream-pass", daemon=True)
        self._thread.start()

    def abort(self) -> None:
        """Discard everything (accidental tap, interrupted recording)."""
        self._stopping.set()
        self._cleanup_queue.put(None)

    def _loop(self) -> None:
        if self._stopping.wait(FIRST_PASS_DELAY):
            return
        while not self._stopping.is_set():
            try:
                self._pass()
            except Exception as exc:  # noqa: BLE001 - a failed pass is not fatal
                logger.debug("streaming pass failed: %s", exc)
            if self._stopping.wait(PASS_INTERVAL):
                return

    # ── incremental cleanup ──────────────────────────────────────────────

    def _schedule_cleanup(self) -> None:
        """Queue everything confirmed since the last block for background
        cleanup.

        Cleanup is the largest single term in key-up → paste (median 1.9 s of a
        4.0 s total on macOS). A confirmed prefix will never change, so it can
        be cleaned while the user is still speaking — by key-up only the
        unconfirmed tail is left, and that is short by construction.
        """
        if self._cleaner is None or self._cleaned_upto >= len(self._confirmed_texts):
            return
        pending = self._confirmed_texts[self._cleaned_upto :]
        words = sum(len(text.split()) for text in pending)
        if words < CLEANUP_BLOCK_WORDS:
            return

        raw = " ".join(pending)
        self._cleaned_upto = len(self._confirmed_texts)
        index = len(self._blocks)
        self._blocks.append({"raw": raw, "cleaned": None})

        if self._cleanup_thread is None:
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_worker, name="stream-cleanup", daemon=True
            )
            self._cleanup_thread.start()
        with self._cleanup_done:
            self._cleanup_pending += 1
        self._cleanup_queue.put((index, raw, words))

    def _cleanup_worker(self) -> None:
        """One Ollama request at a time, in block order."""
        while True:
            item = self._cleanup_queue.get()
            if item is None:
                return
            index, raw, words = item
            started = time.monotonic()
            try:
                cleaned = self._cleaner.clean_block(raw, vocabulary=self._vocabulary)
            except Exception as exc:  # noqa: BLE001 - keep the raw block
                logger.debug("block cleanup failed: %s", exc)
                cleaned = None
            self._blocks[index]["cleaned"] = cleaned
            with self._cleanup_done:
                self._cleanup_pending -= 1
                self._cleanup_done.notify_all()
            if self.debug:
                logger.info(
                    "stream cleaned block %d (%d words) in %.0f ms%s",
                    index, words, (time.monotonic() - started) * 1000,
                    "" if cleaned else " — kept raw",
                )

    def _pre_cleaned_split(self, tail_text: str) -> tuple[str, str] | None:
        """Split the transcript into "already cleaned during recording" and
        "still needs cleaning". Blocks the LLM never answered for fall back to
        raw."""
        if self._cleaner is None or not self._blocks:
            return None
        prefix = " ".join(
            part for part in (b["cleaned"] or b["raw"] for b in self._blocks) if part
        )
        leftover = " ".join(self._confirmed_texts[min(self._cleaned_upto, len(self._confirmed_texts)) :])
        remainder = " ".join(part for part in (leftover, tail_text) if part)
        return prefix, remainder

    # ── passes ───────────────────────────────────────────────────────────

    def _pass(self) -> None:
        everything = self._recorder.snapshot()
        fresh = everything.size - self._confirmed_offset
        if fresh <= MIN_FRESH_SAMPLES:
            return

        slice_end = min(everything.size, self._confirmed_offset + MAX_PASS_SAMPLES)
        audio = everything[self._confirmed_offset : slice_end]
        started = time.monotonic()
        try:
            result = self._transcriber.transcribe(audio)
        except Exception as exc:  # noqa: BLE001 - the batch path still has us covered
            logger.debug("streaming decode failed: %s", exc)
            return
        if self._stopping.is_set():
            return
        self.pass_count += 1

        # Candidates: segments that ended well before the buffer edge.
        slice_seconds = audio.size / SAMPLE_RATE
        stable_end = slice_seconds - STABLE_MARGIN
        candidates: list[tuple[str, float, float]] = []
        for segment in result.segments:
            if not (0 < segment.end <= stable_end):
                continue
            text = strip_special_tokens(segment.text)
            if text:
                candidates.append((text, segment.end, segment.avg_logprob))

        keys = [agreement_key(text) for text, _, _ in candidates]
        # Double agreement: only the prefix both passes read identically.
        agreed = agreed_prefix_count(self._previous_keys, keys) if self._previous_keys else 0

        # Learn this room's noise floor from the slice before judging silence.
        rms_range = window_rms_range(audio)
        if rms_range:
            self._noise_floor = min(self._noise_floor, rms_range[0])
        threshold = silence_threshold(self._noise_floor)

        # Within the agreed prefix, find the LAST candidate whose end sits in
        # real silence — that's the only safe place to cut the audio.
        cut_index: int | None = None
        for i in range(agreed - 1, -1, -1):
            if quiet_cut(audio, candidates[i][1], threshold):
                cut_index = i
                break

        if cut_index is not None:
            for text, _, logprob in candidates[: cut_index + 1]:
                self._confirmed_texts.append(text)
                self._confirmed_logprobs.append(logprob)
            self._confirmed_offset += int(candidates[cut_index][1] * SAMPLE_RATE)
            self._previous_keys = None  # new slice origin → old candidates incomparable
            self._schedule_cleanup()  # clean the new prefix while the user talks on
            if self.debug:
                logger.info(
                    "stream pass %d — confirmed %d segs @%.1fs (slice %.1fs → %.0f ms, floor %.4f)",
                    self.pass_count, cut_index + 1, candidates[cut_index][1],
                    slice_seconds, (time.monotonic() - started) * 1000, threshold,
                )
            return

        self._previous_keys = keys

        # Nothing confirmable for many seconds — almost always a long pause.
        # Gate on raw energy: if the whole stable region is quiet, skip past it
        # so the tail doesn't grow unboundedly.
        if not candidates and slice_seconds > 8:
            stable_samples = min(
                int((slice_seconds - STABLE_MARGIN * 2) * SAMPLE_RATE), audio.size
            )
            if stable_samples > SAMPLE_RATE and max_window_rms(audio, stable_samples) < threshold:
                self._confirmed_offset += stable_samples
                self._previous_keys = None
                if self.debug:
                    logger.info("stream skipped %.1fs of silence", stable_samples / SAMPLE_RATE)

        if self.debug:
            logger.info(
                "stream pass %d — slice %.1fs → %.0f ms, %d candidates, %d agreed, no quiet cut",
                self.pass_count, slice_seconds, (time.monotonic() - started) * 1000,
                len(candidates), agreed,
            )

    # ── finish ───────────────────────────────────────────────────────────

    def finish(self, full_samples: np.ndarray) -> Result:
        """Final result at key-up.

        * V2 off / shadow off: plain full batch decode.
        * Shadow on: stitched + batch both computed, batch returned, diff logged.
        * V2 on: stitched returned; batch only if no prefix was ever confirmed
          (then they are the same decode anyway).
        """
        self._stopping.set()
        if self._thread:
            # Let any in-flight pass settle: the decoder is not reentrant, and
            # the gate would otherwise make the final decode wait on it anyway.
            self._thread.join(timeout=self._transcriber.GATE_WAIT_LIMIT)
        self.last_shadow = None
        self.last_pre_cleaned = None

        # Nothing confirmed → the stitched result IS the batch decode; run once.
        if self._confirmed_offset <= 0 and not self._confirmed_texts:
            return self._transcriber.transcribe(full_samples)

        if v2_enabled() and not shadow_enabled():
            result, tail_text = self._stitched(full_samples)
            # Collect the block cleanups already in flight. They were paid for
            # during the recording; a short settle is worth far more than
            # re-cleaning the same text on the critical path.
            self._settle_cleanup(CLEANUP_SETTLE_SECONDS)
            self.last_pre_cleaned = self._pre_cleaned_split(tail_text)
            return result

        # Shadow: stitched first (tail decode), then the authoritative batch.
        stitched_started = time.monotonic()
        try:
            stitched, _ = self._stitched(full_samples)
        except Exception as exc:  # noqa: BLE001 - the batch below is what we return
            logger.debug("stitched decode failed: %s", exc)
            stitched = None
        stitched_ms = (time.monotonic() - stitched_started) * 1000

        batch_started = time.monotonic()
        batch = self._transcriber.transcribe(full_samples)
        batch_ms = (time.monotonic() - batch_started) * 1000

        if stitched is not None:
            diff = word_diff(stitched.text, batch.text)
            self.last_shadow = ShadowStats(diff, stitched_ms, batch_ms)
            if self.debug:
                logger.info(
                    "shadow — stitched %.0f ms vs batch %.0f ms, diff %d words "
                    "(%d passes, %d confirmed)",
                    stitched_ms, batch_ms, diff, self.pass_count, len(self._confirmed_texts),
                )
        return batch

    def _stitched(self, full_samples: np.ndarray) -> tuple[Result, str]:
        """Decode only the unconfirmed tail and join with the confirmed prefix."""
        offset = min(self._confirmed_offset, full_samples.size)
        tail = full_samples[offset:]
        tail_result = self._transcriber.transcribe(tail)
        joined = collapse_repeats(" ".join(self._confirmed_texts + [tail_result.text]))
        tail_count = len(tail_result.segments)
        total = len(self._confirmed_logprobs) + tail_count
        avg_logprob = (
            (sum(self._confirmed_logprobs) + tail_result.avg_logprob * tail_count) / total
            if total
            else 0.0
        )
        result = Result(
            text=joined,
            avg_logprob=avg_logprob,
            no_speech_prob=tail_result.no_speech_prob,
            segments=tail_result.segments,
        )
        return result, tail_result.text

    def _settle_cleanup(self, seconds: float) -> None:
        """Wait briefly for queued block cleanups, then move on regardless.

        A block that hasn't answered by the deadline keeps its raw text — the
        user gets their words now rather than perfect words late.
        """
        deadline = time.monotonic() + seconds
        with self._cleanup_done:
            while self._cleanup_pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._cleanup_done.wait(remaining)
