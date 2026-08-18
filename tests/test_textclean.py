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


# ── spoken line breaks ───────────────────────────────────────────────────
#
# Enter sends the message in most chat applications, so a dictated multi-line
# note cannot be fixed up by hand afterwards. The risk is the mirror image: a
# phrase eaten when the user meant to say it.

from fortunevoice.textclean import apply_voice_commands  # noqa: E402


def test_a_command_sentence_becomes_a_line_break():
    assert apply_voice_commands("Привет. Новая строка. Как дела?") == "Привет.\nКак дела?"


def test_a_paragraph_command_gives_two_breaks():
    assert apply_voice_commands(
        "Первый пункт. Новый абзац. Второй пункт.") == "Первый пункт.\n\nВторой пункт."


def test_the_phrase_inside_a_sentence_is_left_alone():
    """"Я начал с новой строки" is a sentence ABOUT a line break. Eating it
    would leave the user no way to say the words at all."""
    said = "Я начал с новой строки, потому что так удобнее."
    assert apply_voice_commands(said) == said


def test_case_and_punctuation_do_not_matter():
    assert apply_voice_commands("Слушай. новая строка. записал?") == "Слушай.\nзаписал?"
    assert apply_voice_commands("Раз. НОВАЯ СТРОКА! Два.") == "Раз.\nДва."


def test_no_indent_is_left_after_the_break():
    """The space that separated the two sentences would otherwise sit after
    the newline and read as an indent nobody asked for."""
    assert "\n " not in apply_voice_commands("Раз. Новая строка. Два.")


def test_a_command_at_the_edges_leaves_no_dangling_break():
    assert apply_voice_commands("Новая строка. Текст.") == "Текст."
    assert apply_voice_commands("Текст. Новая строка.") == "Текст."


def test_a_dictation_that_is_only_a_command_still_produces_the_break():
    """They asked for a line break and nothing else. Returning empty would
    make the dictation look like it failed."""
    assert apply_voice_commands("Новая строка") == "\n"


def test_ordinary_text_is_untouched():
    for said in ("Просто обычный текст без команд.",
                 "Переносы строк в этом файле сломаны.",
                 "Let me know if the new line item is approved."):
        assert apply_voice_commands(said) == said


def test_english_commands_work_too():
    assert apply_voice_commands("Hello. New line. World.") == "Hello.\nWorld."


def test_empty_input():
    assert apply_voice_commands("") == ""


# ── keeping the lines a list is made of ──────────────────────────────────


def test_squeeze_lines_keeps_the_line_breaks():
    """The cleanup prompt explicitly asks the model to format an enumeration as
    «- » bullets, one per line. The selective path then rejoined the pieces
    through `squeeze`, which collapses ANY whitespace run — newlines included —
    so the list the model was asked for came back as one flat line:
    "- первое - второе - третье"."""
    from fortunevoice.textclean import squeeze_lines

    listed = "Купить:\n- хлеб\n- молоко\n- кофе"
    assert squeeze_lines(listed) == listed


def test_squeeze_lines_still_collapses_spaces_and_tabs():
    from fortunevoice.textclean import squeeze_lines

    assert squeeze_lines("много   пробелов\tи  табов") == "много пробелов и табов"


def test_squeeze_lines_trims_each_line_and_drops_blank_runs():
    from fortunevoice.textclean import squeeze_lines

    assert squeeze_lines("  раз  \n\n\n  два  ") == "раз\n\nдва"
