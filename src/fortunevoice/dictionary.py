"""Custom vocabulary — names and jargon Whisper keeps mishearing.

Stored as a plain JSON list the user can edit by hand:

    %APPDATA%\\FortuneVoice\\dictionary.json   →   ["Fortune VPN", "Xray", …]

Fed to Whisper as an initial prompt (biases the decoder) and to the cleanup
model as "prefer these terms".
"""

from __future__ import annotations

import json

from . import paths

# Whisper's prompt window is small and every prompt token is decoded on every
# 30 s chunk. The macOS build capped the tokenized prompt at 200 tokens; we cap
# the source string instead, which is the same guard one layer earlier.
MAX_PROMPT_CHARS = 600


def terms() -> list[str]:
    path = paths.dictionary_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def set_terms(values: list[str]) -> None:
    path = paths.dictionary_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# Whisper copies the STYLE of its initial prompt. With an empty one it
# decides punctuation per utterance, which is why a long natural sentence came
# back fully punctuated and a short abrupt one came back as a bare lowercase
# run of words. A punctuated example costs a few tokens and asks for the
# register the user actually writes in.
_STYLE = {
    "ru": "Привет! Вот пример: короткая фраза, запятая, и точка в конце.",
    "en": "Hello! Here is an example: a short phrase, a comma, and a full stop.",
}


def style_prompt() -> str:
    """A punctuated sentence in the dictation language, or "" for auto.

    Not sent when the language is auto-detected: a Russian example in front of
    English audio biases the detector as well as the style, and getting the
    language wrong costs far more than the punctuation is worth.
    """
    from . import config

    return _STYLE.get(config.get_str("FVLanguage"), "")


def prompt_string() -> str:
    """What Whisper is primed with: the style example, then the user's terms."""
    joined = ", ".join(terms())
    style = style_prompt()
    if style and joined:
        joined = f"{style} {joined}"
    elif style:
        joined = style
    return joined[:MAX_PROMPT_CHARS]


# ── learning from corrections ────────────────────────────────────────────

# A term has to clear this to be worth biasing the decoder towards. Short
# words are function words and typos; the prompt window is small and every
# token in it is decoded on every 30 s chunk, so junk here costs accuracy
# everywhere.
MIN_TERM_CHARS = 4
# Never propose more than this from one edit. A user who rewrote a sentence
# wholesale is not teaching us vocabulary, and taking twelve words from that
# would poison the prompt.
MAX_PER_EDIT = 3


def _words(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in "-_":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


# What ends a sentence. The word after one of these is capitalised for
# grammar, not because it is a name.
_TERMINATORS = ".!?…" + chr(10)


def _words_with_sentence_starts(text: str) -> list[tuple[str, bool]]:
    """Each word, and whether it opens a sentence.

    `_words()` discards punctuation, so a caller walking its output cannot
    tell. The flag used to be set True once and never again, which made every
    capitalised word after the first look mid-sentence — and an ordinary word
    opening the second sentence was learned as vocabulary.
    """
    out: list[tuple[str, bool]] = []
    current: list[str] = []
    at_start = True
    pending_start = True
    for ch in text:
        if ch.isalnum() or ch in "-_":
            if not current:
                at_start = pending_start
                pending_start = False
            current.append(ch)
            continue
        if current:
            out.append(("".join(current), at_start))
            current = []
        if ch in _TERMINATORS:
            pending_start = True
    if current:
        out.append(("".join(current), at_start))
    return out


def _looks_like_a_term(word: str, sentence_start: bool) -> bool:
    """Is this a name or a piece of jargon, rather than an ordinary word?

    Two signals, both cheap and both specific to the thing being fixed:
    a capital letter away from the start of a sentence, and Latin letters —
    which in a Russian dictation is by definition a foreign name or a product.
    """
    if len(word) < MIN_TERM_CHARS:
        return False
    if any("a" <= ch.lower() <= "z" for ch in word):
        return True
    return word[:1].isupper() and not sentence_start


def learn_from_correction(before: str, after: str) -> list[str]:
    """Terms the user introduced by correcting a transcript.

    A correction is the one place the app knows for certain that speech
    recognition got a word wrong AND what the right word was — the user just
    typed it. Feeding those back as vocabulary is what stops the same name
    being misheard every single time.

    Returns what was added, so a caller can say so.
    """
    heard = {word.lower() for word in _words(before)}
    known = {term.lower() for term in terms()}

    found: list[str] = []
    for word, starts_sentence in _words_with_sentence_starts(after):
        if (word.lower() not in heard and word.lower() not in known
                and _looks_like_a_term(word, starts_sentence)
                and word not in found):
            found.append(word)

    if not found or len(found) > MAX_PER_EDIT:
        return []
    set_terms([*terms(), *found])
    return found
