"""Ported from RecoveryStoreTests.swift and DictationRecordTests.swift."""

import numpy as np

from fortunevoice.store import DictationRecord, DictationStore, RecoveryStore


def test_saves_and_loads_round_trip(tmp_path):
    store = RecoveryStore(tmp_path)
    samples = (np.sin(np.arange(16_000, dtype=np.float32) * 0.1) * 0.5).astype(np.float32)
    path = store.save(samples)
    assert path is not None

    loaded = store.load(path)
    assert loaded is not None
    assert loaded.size == samples.size
    # 16-bit PCM quantisation: within one LSB of full scale.
    assert float(np.max(np.abs(loaded - samples))) < 1e-4


def test_empty_samples_are_not_saved(tmp_path):
    store = RecoveryStore(tmp_path)
    assert store.save(np.zeros(0, dtype=np.float32)) is None
    assert store.pending() == []


def test_evicts_beyond_the_cap(tmp_path):
    store = RecoveryStore(tmp_path)
    samples = np.zeros(1_600, dtype=np.float32)
    for _ in range(RecoveryStore.MAX_FILES + 3):
        store.save(samples)
    assert len(store.pending()) <= RecoveryStore.MAX_FILES


def test_delete_removes_the_file(tmp_path):
    store = RecoveryStore(tmp_path)
    path = store.save(np.zeros(1_600, dtype=np.float32))
    assert path is not None
    store.delete(path)
    assert store.pending() == []


def test_load_rejects_a_truncated_file(tmp_path):
    store = RecoveryStore(tmp_path)
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFF")
    assert store.load(broken) is None


def test_history_round_trip(tmp_path):
    store = DictationStore(tmp_path / "history.json")
    store.add(
        DictationRecord(
            date="2026-07-30T12:00:00+03:00", words=3, duration=2.5,
            app="Notepad", transcript="раз два три",
        )
    )
    store.add(
        DictationRecord(
            date="2026-07-30T12:01:00+03:00", words=2, duration=1.0,
            app=None, transcript="четыре пять", raw="четыре, пять",
        )
    )
    records = store.all()
    assert [r.words for r in records] == [3, 2]
    assert records[1].raw == "четыре, пять"


def test_history_prune_drops_old_records(tmp_path):
    store = DictationStore(tmp_path / "history.json")
    store.add(
        DictationRecord(
            date="2020-01-01T00:00:00+00:00", words=1, duration=1.0,
            app=None, transcript="старое",
        )
    )
    store.add(
        DictationRecord(
            date="2999-01-01T00:00:00+00:00", words=1, duration=1.0,
            app=None, transcript="новое",
        )
    )
    store.prune(older_than_days=30)
    assert [r.transcript for r in store.all()] == ["новое"]


def test_history_prune_is_a_no_op_at_zero(tmp_path):
    store = DictationStore(tmp_path / "history.json")
    store.add(
        DictationRecord(
            date="2020-01-01T00:00:00+00:00", words=1, duration=1.0,
            app=None, transcript="старое",
        )
    )
    store.prune(older_than_days=0)
    assert len(store.all()) == 1


def test_corrupt_history_reads_as_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{ not json", encoding="utf-8")
    assert DictationStore(path).all() == []


# ── correcting a transcript ──────────────────────────────────────────────


def _one(tmp_path, text="сказал что-то не то"):
    store = DictationStore(tmp_path / "history.json")
    store.add(DictationRecord(date="2026-08-17T10:00:00", words=4, duration=2.0,
                              app="Code.exe", transcript=text))
    return store, store.all()[0]


def test_edit_replaces_the_text(tmp_path):
    store, record = _one(tmp_path)
    assert store.edit(record, "сказал что-то правильное") is True
    assert store.all()[0].transcript == "сказал что-то правильное"


def test_edit_recounts_the_words(tmp_path):
    """Insights are built from this number; leaving it stale would quietly
    skew words-per-minute for every dictation ever corrected."""
    store, record = _one(tmp_path)
    store.edit(record, "одно два три четыре пять")
    assert store.all()[0].words == 5


def test_edit_keeps_the_spoken_words(tmp_path):
    """`raw` is what makes a bad edit recoverable — by the cleanup model or by
    the user. Overwriting it would be the one edit that cannot be undone."""
    store, record = _one(tmp_path, "как было сказано")
    store.edit(record, "как надо")
    assert store.all()[0].raw == "как было сказано"


def test_an_existing_raw_is_not_overwritten(tmp_path):
    """When cleanup already changed the text, `raw` holds the real speech —
    the pre-edit transcript is the model's version, not the user's words."""
    store = DictationStore(tmp_path / "history.json")
    store.add(DictationRecord(date="2026-08-17T10:00:00", words=2, duration=1.0,
                              app=None, transcript="вычитанный текст",
                              raw="ну вот э сырой текст"))
    store.edit(store.all()[0], "исправленный текст")
    assert store.all()[0].raw == "ну вот э сырой текст"


def test_an_empty_or_unchanged_edit_does_nothing(tmp_path):
    store, record = _one(tmp_path)
    assert store.edit(record, "   ") is False
    assert store.edit(record, record.transcript) is False
    assert store.all()[0].transcript == record.transcript


def test_editing_a_record_that_is_gone_reports_it(tmp_path):
    store, record = _one(tmp_path)
    store.remove(record)
    assert store.edit(record, "что-нибудь") is False


def test_edit_leaves_the_neighbours_alone(tmp_path):
    """Matched on content, because the caller holds a row out of a filtered,
    reversed copy — an index into that is not an index into the file."""
    store = DictationStore(tmp_path / "history.json")
    for i in range(3):
        store.add(DictationRecord(date=f"2026-08-17T10:0{i}:00", words=1,
                                  duration=1.0, app=None, transcript=f"строка {i}"))
    store.edit(store.all()[1], "исправлено")
    assert [r.transcript for r in store.all()] == ["строка 0", "исправлено", "строка 2"]


def test_a_failed_write_is_reported_rather_than_swallowed(monkeypatch, tmp_path):
    """The vault-first promise is the app's central claim: the words are saved
    before anything else can go wrong. `_write` logged an OSError and returned
    as if it had worked, so a full disk or a locked file meant dictations
    silently stopped being kept — the one failure the design exists to prevent,
    failing invisibly."""
    from fortunevoice.store import DictationRecord, DictationStore

    store = DictationStore(tmp_path / "history.json")

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(type(tmp_path), "write_text", refuse, raising=False)
    monkeypatch.setattr("pathlib.Path.write_text", refuse)

    assert store.add(DictationRecord(date="2026-08-18T12:00:00", words=1,
                                     duration=1.0, app=None,
                                     transcript="пропало")) is False
