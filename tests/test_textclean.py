"""Ported from CollapseRepeatsTests.swift and the wordDiff cases in
StreamingSessionTests.swift.

Repetition-loop hallucinations must collapse; genuinely distinct phrases,
including near-duplicates, must survive untouched.
"""

from fortunevoice.textclean import collapse_repeats, word_diff


def test_collapses_immediate_identical_fragments():
    assert collapse_repeats("Потерял… Потерял… Потерял…") == "Потерял…"


def test_keeps_distinct_phrases():
    text = "Я опять потерял. Я опять сохранил и потерял."
    assert collapse_repeats(text) == text


def test_collapses_case_and_punctuation_insensitive():
    assert collapse_repeats("Да! да. Да…") == "Да!"


def test_leaves_clean_text_unchanged():
    text = "Завтра встреча в десять утра."
    assert collapse_repeats(text) == text


def test_collapses_run_but_keeps_following():
    assert collapse_repeats("Стоп. Стоп. Стоп. Поехали.") == "Стоп. Поехали."


def test_non_adjacent_repeat_survives():
    # Repetition separated by other content is not a loop — keep both.
    text = "Начали. Работаем. Начали."
    assert collapse_repeats(text) == text


def test_word_diff_identical():
    assert word_diff("раз два три", "раз два три") == 0


def test_word_diff_ignores_punctuation_and_case():
    # Punctuation/case differences are stylistic, not content divergence.
    assert word_diff("раз два", "Раз, два!") == 0


def test_word_diff_insert_delete():
    assert word_diff("раз два три", "раз три") == 1
    assert word_diff("", "раз два") == 2
