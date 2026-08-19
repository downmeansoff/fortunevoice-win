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


def test_keep_alive_seconds_parses_ollama_durations():
    """Used to decide whether pre-loading the model at startup is worth 2.2 GB
    of video memory."""
    from fortunevoice import cleaner, config

    for text, expected in (("0", 0), ("30s", 30), ("5m", 300), ("1h", 3600),
                           ("24h", 86400), ("120", 120)):
        config.set("FVOllamaKeepAlive", text)
        assert cleaner.keep_alive_seconds() == expected, text

    config.set("FVOllamaKeepAlive", "-1")   # Ollama's spelling for "forever"
    assert cleaner.keep_alive_seconds() == float("inf")

    config.set("FVOllamaKeepAlive", "nonsense")
    assert cleaner.keep_alive_seconds() == 300.0, "fall back to the default"


def test_a_word_ending_in_net_is_not_a_self_correction():
    """"нет," was matched as a bare substring, so «интернет,» «момент,»
    «конкурент,» each bought a cleanup round-trip the text did not need."""
    from fortunevoice.cleaner import needs_cleanup

    assert needs_cleanup("Проверь интернет, пожалуйста.") is False
    assert needs_cleanup("Это был важный момент, помнишь?") is False


def test_a_real_self_correction_is_still_caught():
    from fortunevoice.cleaner import needs_cleanup

    assert needs_cleanup("сделай синим, нет, красным") is True
    assert needs_cleanup("нет, подожди") is True


# ── what cleanup is never allowed to do ──────────────────────────────────


def test_a_short_dictation_cannot_be_gutted():
    """The drop guard was off entirely below six words, so "нет я не согласен
    совсем" could come back as "согласен" and pass — every remaining word does
    appear in the raw, so the invented-content check sees nothing wrong."""
    from fortunevoice.cleaner import _kept_enough

    assert _kept_enough("нет я не согласен совсем", "согласен") is False


def test_short_cleanup_that_only_removes_filler_is_still_allowed():
    """The floor exists because short text legitimately loses most of itself:
    "ну вот привет" is three words and "Привет" is the right answer."""
    from fortunevoice.cleaner import _kept_enough

    assert _kept_enough("ну вот привет", "Привет") is True
    assert _kept_enough("э-э короче да", "Да") is True


def test_cleanup_may_not_drop_a_negation():
    """Losing a "не" inverts the sentence. Every other word survives, so the
    invented-content guard passes it, and the ratio guard sees one word out of
    twelve — well inside its tolerance."""
    from fortunevoice.cleaner import _is_safe

    said = ("ну я думаю что мы не будем это делать сегодня потому что "
            "времени совсем не осталось")
    inverted = ("Я думаю, что мы будем это делать сегодня, потому что "
                "времени совсем не осталось.")
    assert _is_safe(said, inverted) is False


def test_an_english_negation_counts_too():
    from fortunevoice.cleaner import _is_safe

    said = "well i do not think we should ship this today at all honestly"
    inverted = "I think we should ship this today at all, honestly."
    assert _is_safe(said, inverted) is False


def test_keeping_the_negations_is_fine():
    """Only filler goes. Every negation the speaker used survives, so the rule
    has nothing to object to."""
    from fortunevoice.cleaner import _is_safe

    said = "ну я думаю что мы не будем это делать сегодня вообще никак"
    cleaned = "Я думаю, что мы не будем это делать сегодня вообще никак."
    assert _is_safe(said, cleaned) is True


def test_losing_one_of_two_negations_is_refused_too():
    """Russian doubles up — "не будем никак" — and dropping the second changes
    what was said. Falling back to the raw text costs the user some polish;
    the alternative costs them the meaning."""
    from fortunevoice.cleaner import _is_safe

    said = "ну я думаю что мы не будем это делать сегодня вообще никак"
    trimmed = "Я думаю, что мы не будем это делать сегодня."
    assert _is_safe(said, trimmed) is False


def test_every_guard_is_still_wired_into_is_safe():
    """Adding the negation rule once dropped the invented-content call from
    `_is_safe` — the tests caught it, and this one names the risk: the guards
    live in one function precisely so none can be lost while editing it."""
    from fortunevoice import cleaner as C

    calls: list[str] = []
    original_kept, original_invented = C._kept_enough, C._no_invented_content
    try:
        C._kept_enough = lambda b, a: calls.append("kept") or True
        C._no_invented_content = lambda b, a: calls.append("invented") or True
        C._is_safe("раз два три четыре пять шесть", "раз два три четыре пять шесть")
    finally:
        C._kept_enough, C._no_invented_content = original_kept, original_invented
    assert calls == ["kept", "invented"]
