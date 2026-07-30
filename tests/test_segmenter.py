"""Ported from Tests/FortuneVoiceTests/TextSegmenterTests.swift."""

from fortunevoice.segmenter import chunk_cores, sentences


def test_splits_on_all_sentence_finals():
    assert [s.strip() for s in sentences("Раз. Два! Три? Четыре… Пять")] == [
        "Раз.", "Два!", "Три?", "Четыре…", "Пять",
    ]


def test_trailing_fragment_without_delimiter_kept():
    assert sentences("привет мир") == ["привет мир"]


def test_empty_and_whitespace_only():
    assert sentences("") == []
    assert sentences("   ") == []


def test_delimiter_stays_with_sentence():
    assert sentences("Что это?Да.") == ["Что это?", "Да."]


def test_single_flagged_sentence():
    assert chunk_cores([False, True, False]) == [(1, 1)]


def test_adjacent_flagged_merge():
    assert chunk_cores([True, True, False, False, False, True]) == [(0, 1), (5, 5)]


def test_gap_within_merge_gap_swallowed():
    # Gap of 1 clean sentence between flagged ones → one core.
    assert chunk_cores([True, False, True]) == [(0, 2)]


def test_gap_beyond_merge_gap_splits():
    # Gap of 2 → separate cores (default merge_gap is 1).
    assert chunk_cores([True, False, False, True]) == [(0, 0), (3, 3)]


def test_no_flags():
    assert chunk_cores([False, False]) == []
    assert chunk_cores([]) == []
