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
