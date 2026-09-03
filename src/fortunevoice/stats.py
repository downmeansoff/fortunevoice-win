"""Aggregates over dictation history.

Port of Sources/FortuneVoice/DictationStats.swift. Pure functions over a list
of records: no I/O, so the tests that came with them port directly.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .store import DictationRecord

UNKNOWN_APP = "Unknown"


def _day(record: DictationRecord) -> date | None:
    try:
        return datetime.fromisoformat(record.date).date()
    except ValueError:
        return None


def total_words(records: list[DictationRecord]) -> int:
    return sum(r.words for r in records)


def words_per_minute(records: list[DictationRecord]) -> float:
    """Words per minute of actual speaking time. 0 when nothing was said,
    never a division by zero."""
    seconds = sum(r.duration for r in records)
    if seconds <= 0:
        return 0.0
    return total_words(records) / (seconds / 60)


def streak_days(records: list[DictationRecord], now: datetime | None = None) -> int:
    """Consecutive days ending today or yesterday that have a dictation.

    Yesterday counts as the anchor too: a streak shouldn't read as broken at
    09:00 just because the user hasn't dictated yet today.
    """
    days = {d for d in (_day(r) for r in records) if d is not None}
    if not days:
        return 0
    today = (now or datetime.now()).date()
    cursor = today if today in days else today - timedelta(days=1)
    if cursor not in days:
        return 0
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def words_by_app(records: list[DictationRecord]) -> list[tuple[str, int]]:
    """(app, words) descending. Records with no app are bucketed as Unknown
    rather than dropped: they are still words the user dictated."""
    totals: dict[str, int] = {}
    for record in records:
        name = record.app or UNKNOWN_APP
        totals[name] = totals.get(name, 0) + record.words
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
