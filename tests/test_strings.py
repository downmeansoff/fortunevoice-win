"""The user-visible text catalogue.

Two languages and ~157 keys, kept in one flat dict. The rules in that module's
docstring are the kind that hold until someone adds a key in a hurry — and the
failure lands on the user, in the language they did not test in.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from fortunevoice import config, strings
from fortunevoice.strings import CATALOGUE, SUPPORTED, t

PLACEHOLDER = re.compile(r"\{(\w+)\}")
SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_every_key_exists_in_every_language():
    """A missing translation falls back to English, which is survivable — but
    silently shipping English into a Russian window is not what anyone meant."""
    missing = [(key, language)
               for key, langs in CATALOGUE.items()
               for language in SUPPORTED
               if not langs.get(language)]
    assert missing == []


def test_placeholders_match_across_languages():
    """`t()` formats with str.format. A key that has {count} in English and
    not in Russian raises in Russian and nowhere else — a crash that only
    appears for users whose UI language nobody ran."""
    mismatched = {
        key: {language: sorted(PLACEHOLDER.findall(langs.get(language, "")))
              for language in SUPPORTED}
        for key, langs in CATALOGUE.items()
        if len({frozenset(PLACEHOLDER.findall(langs.get(language, "")))
                for language in SUPPORTED}) > 1
    }
    assert mismatched == {}


def test_every_key_the_code_asks_for_exists():
    """A typo'd key renders as the key itself — "settings.hotkey" sitting in
    the middle of a window."""
    used: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        used |= set(re.findall(r"""\bt\(\s*["']([a-z_]+\.[a-z_0-9]+)["']""",
                               path.read_text(encoding="utf-8")))
    assert used, "the scan itself must find something"
    assert sorted(used - set(CATALOGUE)) == []


def test_a_missing_key_renders_as_itself_rather_than_raising():
    """A dictation app must not die over a missing string."""
    assert t("no.such.key") == "no.such.key"


def test_formatting_is_applied():
    config.set("FVUILanguage", "en")
    assert "Ctrl+Alt" in t("tray.hold_to_dictate", hotkey="Ctrl+Alt")


def test_the_language_follows_the_setting():
    config.set("FVUILanguage", "ru")
    assert t("nav.settings") == "Настройки"
    config.set("FVUILanguage", "en")
    assert t("nav.settings") == "Settings"


def test_auto_resolves_to_something_supported():
    config.set("FVUILanguage", "auto")
    assert strings.language() in SUPPORTED


def test_an_unknown_language_falls_back_rather_than_breaking():
    config.set("FVUILanguage", "kl")
    assert t("nav.settings") in ("Settings", "Настройки")


@pytest.mark.parametrize("key", ["date.weekday_0", "date.weekday_6",
                                 "date.month_1", "date.month_12"])
def test_the_dynamically_built_keys_are_all_present(key):
    """Built as f"date.weekday_{n}" at call time, so nothing referencing them
    literally exists to catch a gap. History showed «Сегодня», «Вчера», then
    "Saturday" when these were missing."""
    assert key in CATALOGUE


def test_russian_months_are_genitive():
    """The label reads "5 августа 2026", not "5 август 2026"."""
    config.set("FVUILanguage", "ru")
    assert t("date.month_8") == "августа"


def test_no_key_is_left_empty():
    blank = [key for key, langs in CATALOGUE.items()
             if any(not str(value).strip() for value in langs.values())]
    assert blank == []


# ── the README is the only way a friend learns what this does ────────────


def test_every_setting_is_documented():
    """A setting nobody can discover is a setting that does not exist. This is
    handed to friends; the README is the whole of their documentation."""
    from fortunevoice.config import DEFAULTS

    readme = (SOURCE.parent / "README.md").read_text(encoding="utf-8")
    missing = sorted(key for key in DEFAULTS if key not in readme)
    assert missing == []


def test_the_documented_defaults_match_the_code():
    """A README that says ctrl+alt+space while the code says something else
    sends the reader to a shortcut that does nothing."""
    from fortunevoice.config import DEFAULTS

    readme = (SOURCE.parent / "README.md").read_text(encoding="utf-8")
    for key in ("FVHotkey", "FVOllamaModel", "FVOllamaKeepAlive", "FVLanguage"):
        value = str(DEFAULTS[key])
        assert f"`{value}`" in readme, f"{key} default {value!r} is not in the README"


def test_the_features_that_cannot_be_guessed_are_written_down():
    """Nobody works out on their own that saying "new line" makes one, or that
    a mis-delivered dictation can be retyped from the tray."""
    import re

    # Whitespace-normalised: the README wraps at 79 columns, so a phrase can
    # sit across two lines and a literal search would miss it.
    readme = re.sub(r"\s+", " ",
                    (SOURCE.parent / "README.md").read_text(encoding="utf-8").lower())
    for phrase in ("new line", "type last dictation here", "press **esc**"):
        assert phrase in readme, phrase
