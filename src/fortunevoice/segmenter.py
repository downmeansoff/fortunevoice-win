"""Sentence splitting shared by repeat-collapse and selective cleanup.

Direct port of Sources/FortuneVoice/TextSegmenter.swift. Deliberately simple:
split on sentence-final punctuation, keep the delimiter with its sentence.
Dictated text has no abbreviations-with-dots worth special-casing.
"""

from __future__ import annotations

_TERMINATORS = ".!?…"


def sentences(text: str) -> list[str]:
    """Sentence-like fragments, each keeping its trailing delimiter. The final
    fragment is included even without one (trailing speech)."""
    fragments: list[str] = []
    current: list[str] = []
    for ch in text:
        current.append(ch)
        if ch in _TERMINATORS:
            fragments.append("".join(current))
            current = []
    tail = "".join(current)
    if tail.strip():
        fragments.append(tail)
    return fragments


def chunk_cores(flags: list[bool], merge_gap: int = 1) -> list[tuple[int, int]]:
    """Merge flagged sentence indices into inclusive (start, end) cores.

    Runs of flagged sentences swallow gaps of up to `merge_gap` clean ones
    between them — one LLM call per core beats separate calls for near
    neighbors. Gap of 1, not more: generation cost is proportional to core
    length, and greedy merging produced one huge chunk that blew the cleanup
    deadline on long dictations.
    """
    cores: list[tuple[int, int]] = []
    for i, flagged in enumerate(flags):
        if not flagged:
            continue
        if cores and i - cores[-1][1] - 1 <= merge_gap:
            cores[-1] = (cores[-1][0], i)
        else:
            cores.append((i, i))
    return cores
