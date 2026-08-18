"""Custom vocabulary, and learning it from corrections.

A correction is the one place the app knows for certain that recognition got a
word wrong AND what the right word was, because the user just typed it. The
danger is the opposite one: junk in the prompt costs accuracy on every single
dictation, since every prompt token is decoded on every 30 s chunk.
"""

from __future__ import annotations

from fortunevoice import dictionary


def test_a_foreign_name_is_learned():
    assert dictionary.learn_from_correction(
        "надо поднять хецнер", "надо поднять Hetzner") == ["Hetzner"]
    assert "Hetzner" in dictionary.terms()


def test_a_capitalised_name_mid_sentence_is_learned():
    assert dictionary.learn_from_correction(
        "позвони Кате", "позвони Катерине") == ["Катерине"]


def test_an_ordinary_word_swap_teaches_nothing():
    """Changing "привет" to "пока" is a correction, not a term."""
    assert dictionary.learn_from_correction("я сказал привет", "я сказал пока") == []
    assert dictionary.terms() == []


def test_the_first_word_of_a_sentence_is_not_a_name():
    """It is capitalised for grammar. Taking it would fill the prompt with
    ordinary words, which costs accuracy everywhere."""
    assert dictionary.learn_from_correction("привет всем", "Здравствуйте всем") == []


def test_short_words_are_ignored():
    assert dictionary.learn_from_correction("сказал да", "сказал Ok") == []


def test_a_wholesale_rewrite_teaches_nothing():
    """A user who rewrote the sentence is not teaching vocabulary, and taking
    a dozen words from it would poison the prompt."""
    assert dictionary.learn_from_correction(
        "всё не так", "Совсем Другой Текст Про Другое Дело") == []


def test_a_term_is_not_learned_twice():
    dictionary.set_terms(["Hetzner"])
    assert dictionary.learn_from_correction("поднять хецнер", "поднять Hetzner") == []
    assert dictionary.terms() == ["Hetzner"]


def test_learning_appends_rather_than_replaces():
    dictionary.set_terms(["Fortune VPN"])
    dictionary.learn_from_correction("про экс рей", "про Xray")
    assert dictionary.terms() == ["Fortune VPN", "Xray"]


def test_a_learned_term_reaches_the_prompt():
    """The whole point: what was learned has to bias the next decode."""
    dictionary.learn_from_correction("поднять хецнер", "поднять Hetzner")
    assert "Hetzner" in dictionary.prompt_string()


def test_the_prompt_stays_within_its_cap():
    dictionary.set_terms([f"термин{i}" for i in range(200)])
    assert len(dictionary.prompt_string()) <= dictionary.MAX_PROMPT_CHARS


def test_a_missing_or_broken_file_is_not_a_crash():
    """A dictation app must not die over a hand-edited JSON file."""
    from fortunevoice import paths

    paths.dictionary_file().write_text("{ not a list", encoding="utf-8")
    assert dictionary.terms() == []
    paths.dictionary_file().write_text('{"a": 1}', encoding="utf-8")
    assert dictionary.terms() == []


# ── learning from a correction ───────────────────────────────────────────


def test_a_capitalised_word_opening_a_later_sentence_is_not_learned():
    """`_words()` throws punctuation away, so the sentence-start flag was set
    once and never again: every capitalised word after the first counted as
    mid-sentence. An ordinary word opening the second sentence was learned as
    vocabulary and then fed to Whisper as a prompt bias on every dictation —
    the opposite of what the dictionary is for."""
    dictionary.set_terms([])
    learned = dictionary.learn_from_correction(
        "я закончил дождь будет",
        "Я закончил. Завтра будет дождь.",
    )
    assert learned == [], f"«Завтра» не термин, а выучено: {learned}"


def test_a_real_name_mid_sentence_is_still_learned():
    dictionary.set_terms([])
    learned = dictionary.learn_from_correction(
        "позвони ему завтра", "Позвони Андрею завтра.")
    assert learned == ["Андрею"]


def test_a_latin_term_is_learned_wherever_it_sits():
    """Latin letters in a Russian dictation are a foreign name or a product by
    definition, so those count even at the start of a sentence."""
    dictionary.set_terms([])
    assert dictionary.learn_from_correction(
        "подними сервер", "Hetzner подними.") == ["Hetzner"]


def test_nothing_is_learned_when_only_the_case_changed():
    dictionary.set_terms([])
    assert dictionary.learn_from_correction("привет мир", "Привет мир") == []
