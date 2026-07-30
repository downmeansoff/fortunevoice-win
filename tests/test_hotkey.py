"""Hotkey parsing. No Windows API is touched — only the string → (modifiers,
virtual key) mapping, which is where a typo in config.json turns into a hotkey
that silently never fires."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice.hotkey import parse  # noqa: E402


def test_default_chord():
    spec = parse("ctrl+alt+space")
    assert spec.modifiers == ["ctrl", "alt"]
    assert spec.key == 0x20


def test_single_key():
    spec = parse("f9")
    assert spec.modifiers == []
    assert spec.key == 0x78


def test_case_and_spacing_insensitive():
    assert parse("  CTRL + Space ").key == parse("ctrl+space").key


def test_modifier_as_trigger():
    # "hold right ctrl to talk" — no modifiers required alongside it.
    spec = parse("rctrl")
    assert spec.modifiers == []
    assert spec.key == 0xA3


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError, match="unknown key"):
        parse("ctrl+spacebar")


def test_unknown_modifier_is_rejected():
    with pytest.raises(ValueError, match="unknown modifier"):
        parse("meta+space")


def test_empty_is_rejected():
    with pytest.raises(ValueError):
        parse("   ")
