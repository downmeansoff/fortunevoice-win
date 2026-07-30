"""Deterministic transcript repairs that run regardless of the LLM cleanup.

Port of Transcriber.collapseRepeats from the macOS build.
"""

from __future__ import annotations

import re

from .segmenter import sentences

_WHITESPACE = re.compile(r"\s+")
# Whisper emits control tokens like <|0.00|> or <|nospeech|> when timestamps
# are on; they must never reach the user's text field.
_SPECIAL_TOKEN = re.compile(r"<\|[^|]*\|>")


def normalize_key(text: str) -> str:
    """Case- and punctuation-insensitive comparison key."""
    kept = "".join(ch for ch in text.lower() if ch.isalnum() or ch.isspace())
    return _WHITESPACE.sub(" ", kept).strip()


def strip_special_tokens(text: str) -> str:
    return _SPECIAL_TOKEN.sub("", text).strip()


def squeeze(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def collapse_repeats(text: str) -> str:
    """Undo Whisper repetition-loop hallucinations.

    When a sentence-like fragment repeats immediately (identical ignoring case
    and punctuation), keep only the first. Handles "Потерял… Потерял… Потерял…"
    without touching genuinely distinct phrases. Runs always, independent of
    the optional LLM cleanup.
    """
    kept: list[str] = []
    last_key = ""
    for fragment in sentences(text):
        key = normalize_key(fragment)
        if not key:
            continue
        if key == last_key:
            continue  # drop the immediate duplicate
        last_key = key
        kept.append(fragment.strip())
    return squeeze(" ".join(kept))


def word_count(text: str) -> int:
    return len(text.split())


def word_diff(a: str, b: str) -> int:
    """Word-level Levenshtein distance on CONTENT only.

    Words are normalized (case and punctuation stripped) before comparison:
    the stitched and batch decodes legitimately differ in comma placement, and
    counting that as divergence would make every shadow run look broken.
    Capped input keeps the DP cheap; beyond the cap the length difference is
    added linearly.
    """
    cap = 600

    def words(text: str) -> list[str]:
        out = []
        for token in text.split():
            stripped = "".join(ch for ch in token.lower() if ch.isalnum())
            if stripped:
                out.append(stripped)
        return out

    wa, wb = words(a), words(b)
    overflow = 0
    if len(wa) > cap or len(wb) > cap:
        overflow = abs(len(wa) - len(wb))
        wa, wb = wa[:cap], wb[:cap]
    if not wa:
        return len(wb) + overflow
    if not wb:
        return len(wa) + overflow

    prev = list(range(len(wb) + 1))
    for i, ca in enumerate(wa, start=1):
        cur = [i] + [0] * len(wb)
        for j, cb in enumerate(wb, start=1):
            cur[j] = prev[j - 1] if ca == cb else min(prev[j - 1], prev[j], cur[j - 1]) + 1
        prev = cur
    return prev[len(wb)] + overflow
