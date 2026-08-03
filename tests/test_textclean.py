import pytest
"""Ported from CollapseRepeatsTests.swift and the wordDiff cases in
StreamingSessionTests.swift.

Repetition-loop hallucinations must collapse; genuinely distinct phrases,
including near-duplicates, must survive untouched.
"""

from fortunevoice.textclean import is_hallucinated_silence, collapse_repeats, word_diff


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


# ── silence hallucinations ───────────────────────────────────────────────


@pytest.mark.parametrize("rms", [0.00029, 0.00076, 0.00080, 0.00146])
def test_measured_room_silence_is_rejected(rms):
    """The four RMS values recorded from a real laptop microphone in a quiet
    room, each of which Whisper transcribed as "Продолжение следует." with
    no_speech_prob = 0.000 — total confidence that the room noise was speech."""
    assert is_hallucinated_silence("Продолжение следует.", rms) is True


@pytest.mark.parametrize(("text", "rms"), [
    ("Завтра встреча в десять утра.", 0.08),
    ("да", 0.02),
    ("It is important to review the pull request today.", 0.06),
])
def test_real_speech_is_never_rejected(text, rms):
    assert is_hallucinated_silence(text, rms) is False


def test_someone_actually_saying_the_phrase_is_kept():
    """The phrase list must not censor a person. Said out loud it arrives at
    speech volume, and volume is what decides."""
    assert is_hallucinated_silence("Продолжение следует.", 0.15) is False


@pytest.mark.parametrize("text", [
    "Спасибо за просмотр",
    "Субтитры сделал DimaTorzok",
    "Thanks for watching!",
])
def test_subtitle_boilerplate_over_a_noisy_room_is_rejected(text):
    """A fan or street noise can push RMS over the silence floor while the room
    still holds no speech, so the phrase list is a second net in that band."""
    assert is_hallucinated_silence(text, 0.012) is True


def test_the_phrase_net_stops_at_speech_volume():
    """Above the band, only the audio decides — no phrase is ever blocked at a
    volume a person actually reaches."""
    assert is_hallucinated_silence("Спасибо за просмотр", 0.05) is False
