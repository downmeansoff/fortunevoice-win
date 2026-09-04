"""Per-dictation timings.

Every tuning decision in this app (the cleanup cost model, the streaming
defaults, the tail-capture length) came from reading this file. Numbers that
are quietly wrong are worse than no numbers, because they are still trusted.
"""

from __future__ import annotations

import json

from fortunevoice import metrics, paths


def a_metric(**overrides) -> metrics.DictationMetric:
    fields = dict(
        date=metrics.now(), capture_sec=1.9, stt_ms=407.0, cleanup_ms=469.0,
        total_ms=828.0, chars=42, outcome="typed", cleanup_skipped=False,
        retried=False, stream_passes=3, logprob=-0.24, model="large-v3-turbo",
    )
    fields.update(overrides)
    return metrics.DictationMetric(**fields)


def test_a_metric_survives_the_round_trip():
    metrics.record(a_metric(chars=42, outcome="typed"))
    rows = metrics.read_all()
    assert len(rows) == 1
    assert rows[0]["chars"] == 42
    assert rows[0]["outcome"] == "typed"
    assert rows[0]["model"] == "large-v3-turbo"


def test_records_append_rather_than_replace():
    """`stats` reads the whole history; a truncating write would silently
    reduce it to the last dictation."""
    for i in range(3):
        metrics.record(a_metric(chars=i))
    assert [r["chars"] for r in metrics.read_all()] == [0, 1, 2]


def test_one_line_per_dictation():
    metrics.record(a_metric())
    text = paths.metrics_file().read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert len([line for line in text.splitlines() if line.strip()]) == 1


def test_optional_fields_are_written_even_when_empty():
    """The macOS build's fields are kept so the two ports' numbers stay
    comparable; a field that vanishes when unset is a field that cannot be
    compared."""
    metrics.record(a_metric())
    row = metrics.read_all()[0]
    for field in ("device", "cleanup_chunks", "shadow_diff_words", "batch_ms"):
        assert field in row


def test_a_broken_line_does_not_lose_the_rest():
    """What a power cut mid-append leaves behind. Losing the whole history to
    one half-written line would be the file destroying itself."""
    metrics.record(a_metric(chars=1))
    with paths.metrics_file().open("a", encoding="utf-8") as handle:
        handle.write('{"chars": 2, "trunc')          # cut off mid-write
    metrics.record(a_metric(chars=3))
    assert [r["chars"] for r in metrics.read_all()] == [1, 3]


def test_blank_lines_are_skipped():
    metrics.record(a_metric(chars=1))
    with paths.metrics_file().open("a", encoding="utf-8") as handle:
        handle.write("\n   \n")
    assert len(metrics.read_all()) == 1


def test_reading_a_file_that_does_not_exist():
    assert metrics.read_all() == []


def test_recording_never_raises_when_the_file_cannot_be_written(monkeypatch):
    """Metrics are diagnostics. A dictation must never fail because of them."""
    monkeypatch.setattr(paths, "metrics_file",
                        lambda: paths.home() / "no-such-dir" / "metrics.jsonl")
    metrics.record(a_metric())  # must not raise


def test_unicode_is_kept_readable():
    """The file is meant to be opened and read; \\u0434\\u0438\\u043a escapes are not."""
    metrics.record(a_metric(outcome="диктовка"))
    assert "диктовка" in paths.metrics_file().read_text(encoding="utf-8")


def test_now_is_iso_with_an_offset():
    """Timestamps compared against history entries; a naive one would sort
    wrongly against them across a timezone change."""
    stamp = metrics.now()
    assert "T" in stamp
    assert stamp[-6] in "+-" or stamp.endswith("Z"), stamp


def test_a_small_file_is_never_trimmed():
    for i in range(50):
        metrics.record(a_metric(chars=i))
    assert len(metrics.read_all()) == 50


def test_trimming_keeps_the_newest(monkeypatch):
    """A year of heavy use is ~130k lines. When the cap does bite, the lines
    worth keeping are the recent ones: the cost model is fitted on those."""
    monkeypatch.setattr(metrics, "MAX_LINES", 10)
    real = paths.metrics_file()
    real.write_text("".join(json.dumps({"chars": i}) + "\n" for i in range(40)),
                    encoding="utf-8")

    class PretendsToBeHuge:
        """The real file, reporting a size past the trim threshold. Patching
        Path.stat globally instead breaks pathlib for everything else."""

        def __init__(self, path):
            self._path = path

        def stat(self):
            import types

            return types.SimpleNamespace(st_size=40_000_000)

        def read_text(self, **kwargs):
            return self._path.read_text(**kwargs)

        def write_text(self, text, **kwargs):
            return self._path.write_text(text, **kwargs)

        def __fspath__(self):
            # `tmp.replace(path)` needs a real path on the other side.
            return str(self._path)

        def with_suffix(self, suffix):
            # The trim writes through a temp file and renames, so a write
            # interrupted mid-way cannot leave the live file truncated.
            return self._path.with_suffix(suffix)

    metrics._trim(PretendsToBeHuge(real))
    rows = metrics.read_all()
    assert len(rows) == 10
    assert [r["chars"] for r in rows] == list(range(30, 40))


def test_the_repaired_line_is_not_glued_to_the_next_one():
    """The half-written line stays broken and is skipped, but it must not take
    the following record with it."""
    metrics.record(a_metric(chars=1))
    with paths.metrics_file().open("a", encoding="utf-8") as handle:
        handle.write('{"chars": 2, "trunc')
    metrics.record(a_metric(chars=3))

    lines = paths.metrics_file().read_text(encoding="utf-8").splitlines()
    assert lines[1] == '{"chars": 2, "trunc', "the damaged line is left as it is"
    assert json.loads(lines[2])["chars"] == 3, "and the next one is whole"


def test_one_torn_byte_does_not_take_the_whole_file_with_it(tmp_path, monkeypatch):
    """A machine that loses power mid-append leaves a Cyrillic character cut
    in half. UnicodeDecodeError is a ValueError, not an OSError, so it went
    straight past the guard and out of the Insights page and out of `doctor
    stats`, permanently, until the user found and hand-edited the file."""
    target = tmp_path / "metrics.jsonl"
    newline = chr(10).encode("utf-8")
    good = json.dumps({"date": "2026-08-28", "chars": 7}).encode("utf-8")
    later = json.dumps({"date": "2026-08-28", "chars": 9}).encode("utf-8")
    half_a_character = b'{"date": "2026-08-28", "app": "' + bytes([0xD0])

    target.write_bytes(good + newline + half_a_character + newline
                       + later + newline)
    monkeypatch.setattr(paths, "metrics_file", lambda: target)

    rows = metrics.read_all()

    assert [r["chars"] for r in rows] == [7, 9], "the intact records must survive"
