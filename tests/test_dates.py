"""Date labels in History.

`strftime("%A")` answers in the C locale — English, whatever the UI language.
The result was a list headed «Сегодня», «Вчера», then "Saturday": two thirds
translated, which reads worse than none of it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fortunevoice import config
from fortunevoice.ui.main_window import _day_label

TODAY = dt.date(2026, 8, 5)  # a Wednesday


@pytest.fixture
def russian(monkeypatch):
    """`language()` reads the config on every call, so patching it is enough."""
    monkeypatch.setattr(config, "get_str", lambda key, default="": (
        "ru" if key == "FVUILanguage" else default))


def test_recent_days_are_translated(russian):
    assert _day_label((TODAY - dt.timedelta(days=3)).isoformat(), TODAY) == "Воскресенье"
    assert _day_label((TODAY - dt.timedelta(days=5)).isoformat(), TODAY) == "Пятница"


def test_today_and_yesterday_win_over_the_weekday(russian):
    assert _day_label(TODAY.isoformat(), TODAY) == "Сегодня"
    assert _day_label((TODAY - dt.timedelta(days=1)).isoformat(), TODAY) == "Вчера"


def test_older_dates_use_the_genitive_month(russian):
    """"16 июля", not "16 июль" — the nominative reads like a machine wrote it."""
    assert _day_label("2026-07-16T10:00:00", TODAY) == "16 июля 2026"


def test_a_broken_timestamp_never_raises(russian):
    """History is drawn from a JSON file the user can edit by hand."""
    assert _day_label("not a date", TODAY) == "Раньше"


def test_every_weekday_and_month_has_both_languages():
    """A missing key falls back to the key itself, which would print
    "date.month_9" into the window."""
    from fortunevoice.strings import CATALOGUE

    for index in range(7):
        entry = CATALOGUE[f"date.weekday_{index}"]
        assert entry["en"] and entry["ru"]
    for index in range(1, 13):
        entry = CATALOGUE[f"date.month_{index}"]
        assert entry["en"] and entry["ru"]
