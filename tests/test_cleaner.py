"""Ported from NeedsCleanupTests.swift and CleanupBudgetTests.swift."""

from fortunevoice.cleaner import needs_cleanup, predicted_ms

BUDGET_MS = 1.5 * 1000


# ── needs_cleanup ────────────────────────────────────────────────────────
# The skip heuristic trades ~1 s of latency against leaving artifacts in, so
# both directions matter: never skip text that needs fixing, never burn a
# round-trip on text that doesn't.


def test_detects_stumble_repetition():
    assert needs_cleanup("нужно правильно это правильно писать")
    assert needs_cleanup("я я думаю это верно")


def test_detects_filler():
    assert needs_cleanup("ну я хотел сказать")
    assert needs_cleanup("это, короче, важно")
    assert needs_cleanup("It is, um, important")
    assert needs_cleanup("это как бы работает")


def test_detects_self_correction():
    assert needs_cleanup("сделай синим, нет, красным")
    assert needs_cleanup("во вторник, то есть в среду")


def test_skips_clean_text():
    assert not needs_cleanup("Завтра встреча в десять утра.")
    assert not needs_cleanup("Please review the pull request today.")
    assert not needs_cleanup("Сделай превью скриншотов.")


def test_filler_substring_does_not_false_trigger():
    # "ну" inside a word must not count as filler.
    assert not needs_cleanup("Нужно проверить нумерацию.")
    # "вот" inside "поворот".
    assert not needs_cleanup("Здесь резкий поворот.")


def test_repeated_short_words_not_over_triggered():
    # Legitimate repetition of a distinct pair shouldn't be flagged.
    assert not needs_cleanup("Он пришёл и ушёл.")


# ── the cost model ───────────────────────────────────────────────────────
# It is what decides whether a cleanup runs at all, so it has to stay anchored
# to the live-run regression it came from: gemma3:4b, 908 ms + 6.36 ms/char.


def test_empty_text_costs_only_the_round_trip():
    assert predicted_ms(0) == 900


def test_cost_grows_with_length():
    assert predicted_ms(40) < predicted_ms(400)


def test_matches_the_measured_rate():
    # 160 chars ≈ 40 tokens ≈ 1 s of generation on top of the round-trip.
    assert abs(predicted_ms(160) - 1900) <= 1


def test_short_dictation_fits_the_budget():
    # The common case the user notices must stay cleanable.
    assert predicted_ms(80) <= BUDGET_MS


def test_long_transcript_is_declined_up_front():
    # Metrics had 24 of 60 runs starting work like this and having it thrown
    # away at the deadline, costing the user 3 s for the raw text anyway.
    assert predicted_ms(700) > BUDGET_MS


def test_refuses_the_lengths_that_used_to_be_discarded():
    # Live runs the 400 ms intercept wrongly cleared: 2077 ms and 2028 ms.
    assert predicted_ms(168) > BUDGET_MS
    assert predicted_ms(192) > BUDGET_MS


def test_still_accepts_the_lengths_that_landed_in_time():
    # 1184 ms and 1559 ms measured — guards against over-correcting into
    # "never clean".
    assert predicted_ms(64) <= BUDGET_MS
    assert predicted_ms(82) <= BUDGET_MS


def test_keep_alive_comes_from_config_not_code():
    """It was pinned at 24h, which held 2.2 GB of video memory permanently and
    made the whole desktop stutter on a 6 GB card."""
    from fortunevoice import cleaner, config

    assert cleaner.keep_alive() == "5m", "a short hold is the default now"
    config.set("FVOllamaKeepAlive", "24h")
    assert cleaner.keep_alive() == "24h"
    config.set("FVOllamaKeepAlive", "0")
    assert cleaner.keep_alive() == "0", "unloading at once must be expressible"


def test_keep_alive_never_returns_empty():
    """An empty value would make Ollama fall back to its own default rather
    than to ours, so the setting would appear to do nothing."""
    from fortunevoice import cleaner, config

    config.set("FVOllamaKeepAlive", "")
    assert cleaner.keep_alive() == "5m"
