"""Ported from Tests/FortuneVoiceTests/DictationStatsTests.swift."""

from datetime import datetime, timedelta

from fortunevoice.stats import streak_days, total_words, words_by_app, words_per_minute
from fortunevoice.store import DictationRecord

NOW = datetime(2026, 7, 30, 14, 0, 0)


def rec(days_ago: int, words: int, duration: float, app: str | None = None) -> DictationRecord:
    when = NOW - timedelta(days=days_ago)
    return DictationRecord(
        date=when.isoformat(), words=words, duration=duration, app=app, transcript="x"
    )


def test_total_words():
    assert total_words([rec(0, 10, 5), rec(1, 5, 3)]) == 15


def test_words_per_minute():
    # 30 words over 60 s of speech = 30 wpm
    assert words_per_minute([rec(0, 30, 60)]) == 30


def test_words_per_minute_empty():
    assert words_per_minute([]) == 0


def test_streak_consecutive():
    records = [rec(0, 1, 1), rec(1, 1, 1), rec(2, 1, 1)]
    assert streak_days(records, now=NOW) == 3


def test_streak_broken_by_gap():
    records = [rec(0, 1, 1), rec(1, 1, 1), rec(3, 1, 1)]  # gap at day 2
    assert streak_days(records, now=NOW) == 2


def test_streak_survives_no_dictation_today():
    # Nothing today, but yesterday + the day before: the streak still counts.
    records = [rec(1, 1, 1), rec(2, 1, 1)]
    assert streak_days(records, now=NOW) == 2


def test_streak_empty():
    assert streak_days([], now=NOW) == 0


def test_words_by_app_sorted_descending():
    records = [
        rec(0, 5, 1, "Telegram"),
        rec(0, 10, 1, "Mail"),
        rec(0, 3, 1, "Telegram"),
        rec(0, 2, 1, None),
    ]
    result = words_by_app(records)
    assert result[0] == ("Mail", 10)
    assert result[1] == ("Telegram", 8)  # 5 + 3
    assert ("Unknown", 2) in result
