"""The self-correcting cost model, deleting one dictation, and window geometry.

Each of these has a failure mode that is silent rather than loud, which is why
they are pinned here:

* a cost fit that learns the wrong slope makes cleanup quietly stop running,
* deleting by list position removes the wrong row when the list is filtered,
* a remembered geometry from an unplugged monitor puts the window somewhere
  the user cannot reach it.
"""

from __future__ import annotations

import pytest

from fortunevoice import cleaner
from fortunevoice.store import DictationRecord, DictationStore


@pytest.fixture(autouse=True)
def _fresh_fit():
    cleaner.reset_fit()
    yield
    cleaner.reset_fit()


# ── the learned cleanup cost model ───────────────────────────────────────


def _rows(pairs, model=None, **extra):
    """Metric rows the fit will accept.

    `cleanup_model` is required now: `model` in a metric is the WHISPER model,
    so without this the fit pooled runs from different cleanup models (1141 ms
    against 656 ms on the same text), and fitted a line through both.
    """
    from fortunevoice import config

    name = model or config.get_str("FVOllamaModel")
    return [{"chars": c, "cleanup_ms": ms, "cleanup_model": name, **extra}
            for c, ms in pairs]


def test_fit_needs_enough_samples(monkeypatch):
    """Two runs are an anecdote. The shipped constants stay until there is
    enough local data to beat them."""
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(_rows([(40, 300), (80, 400)])),
                        raising=False)
    assert cleaner._fit_from_metrics() is None


def test_fit_learns_this_machines_cost(monkeypatch):
    """The real case this exists for: qwen2.5:3b on a 3060 answers in ~280 ms
    where the shipped gemma-on-Apple-silicon fit predicts 1200+, so the budget
    was declining work it could comfortably afford."""
    pairs = [(chars, 250 + chars * 1.5) for chars in range(20, 200, 10)]
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(_rows(pairs)), raising=False)
    fixed, per_char = cleaner._fit_from_metrics()
    assert fixed == pytest.approx(250, abs=15)
    assert per_char == pytest.approx(1.5, abs=0.05)
    # The whole point: a 124-char job now fits inside the 1.5 s budget.
    assert fixed + 124 * per_char < 1500


def test_fit_is_floored(monkeypatch):
    """A run of cache-hot samples can regress to a near-zero intercept. Trusting
    it would clear work the budget cannot actually afford: the exact waste the
    predictor exists to prevent."""
    pairs = [(chars, 5 + chars * 0.9) for chars in range(20, 200, 10)]
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(_rows(pairs)), raising=False)
    fixed, _ = cleaner._fit_from_metrics()
    assert fixed == cleaner.MIN_FIXED_MS


def test_fit_rejects_a_negative_slope(monkeypatch):
    """Longer text coming out cheaper is noise, not a model."""
    pairs = [(chars, 900 - chars) for chars in range(20, 200, 10)]
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(_rows(pairs)), raising=False)
    assert cleaner._fit_from_metrics() is None


def test_predicted_ms_falls_back_without_data(monkeypatch):
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics([]), raising=False)
    assert cleaner.predicted_ms(0) == cleaner.FALLBACK_FIXED_MS
    assert cleaner.predicted_ms(100) == pytest.approx(
        cleaner.FALLBACK_FIXED_MS + 100 * cleaner.FALLBACK_PER_CHAR_MS)


class _FakeMetrics:
    def __init__(self, rows):
        self._rows = rows

    def read_all(self):
        return self._rows


# ── deleting one dictation ───────────────────────────────────────────────


def _record(minute: int, text: str) -> DictationRecord:
    return DictationRecord(date=f"2026-07-31T10:{minute:02d}:00", words=len(text.split()),
                           duration=1.0, app="Editor", transcript=text)


def test_remove_deletes_the_matching_record(tmp_path):
    store = DictationStore(tmp_path / "history.json")
    for index, text in enumerate(["первая", "вторая", "третья"]):
        store.add(_record(index, text))

    assert store.remove(_record(1, "вторая")) is True
    assert [r.transcript for r in store.all()] == ["первая", "третья"]


def test_remove_is_matched_on_content_not_position(tmp_path):
    """The UI list is filtered and reversed, so its indices are not the file's.
    Deleting by position would remove a neighbour."""
    store = DictationStore(tmp_path / "history.json")
    for index, text in enumerate(["alpha", "beta", "gamma"]):
        store.add(_record(index, text))

    reversed_view = list(reversed(store.all()))
    store.remove(reversed_view[0])  # newest, which is LAST in the file
    assert [r.transcript for r in store.all()] == ["alpha", "beta"]


def test_remove_reports_a_miss(tmp_path):
    store = DictationStore(tmp_path / "history.json")
    store.add(_record(0, "one"))
    assert store.remove(_record(9, "never stored")) is False
    assert len(store.all()) == 1


# ── remembered window geometry ───────────────────────────────────────────


@pytest.mark.parametrize("saved", ["", "not a geometry", "900x600"])
def test_geometry_falls_back_to_the_default_size(saved, monkeypatch):
    from fortunevoice.ui import main_window

    monkeypatch.setattr(main_window.config, "get_str", lambda _key: saved)
    result = main_window._remembered_geometry()
    assert "+" not in result
    assert "x" in result


def test_geometry_keeps_an_on_screen_position(monkeypatch):
    from fortunevoice.ui import main_window

    monkeypatch.setattr(main_window.config, "get_str", lambda _key: "900x600+120+80")
    monkeypatch.setattr(main_window.winapi, "work_area_of_window",
                        lambda _hwnd: (0, 0, 1920, 1040))
    assert main_window._remembered_geometry() == "900x600+120+80"


def test_geometry_drops_a_position_on_a_vanished_monitor(monkeypatch):
    """Saved while a second screen was plugged in; without this the window
    reopens at x=2600 on a machine that now has one 1920-wide display, with no
    way to drag it back."""
    from fortunevoice.ui import main_window

    monkeypatch.setattr(main_window.config, "get_str", lambda _key: "900x600+2600+400")
    monkeypatch.setattr(main_window.winapi, "work_area_of_window",
                        lambda _hwnd: (0, 0, 1920, 1040))
    assert main_window._remembered_geometry() == "900x600"


# ── the invented-content guard ───────────────────────────────────────────


def test_invented_content_is_rejected():
    """The failure this exists for, measured on this machine: qwen2.5:1.5b
    turned one sentence into a different one of the same length. The 35% guard
    only catches deletion, so substitution went straight through it, and
    substitution is worse, because the app types it."""
    said = "Ну это самое, короче, надо бы проверить, как работает диктовка."
    invented = "Ну это самое, короче, нужно правильно это писать."
    assert cleaner._no_invented_content(said, invented) is False
    assert cleaner._is_safe(said, invented) is False


@pytest.mark.parametrize(("said", "cleaned", "why"), [
    ("Нужно правильно это правильно писать в отчёте.",
     "Нужно правильно это писать в отчёте.", "stumble collapsed"),
    ("Сделай кнопку синим, нет, красным цветом.",
     "сделай красным цветом", "self-correction applied"),
    ("It is, um, important to review the pull request today.",
     "It is important to review the pull request today.", "filler removed"),
    ("Завтра встреча в десять утра.", "Завтра встреча в десять утра.", "untouched"),
    ("нужно правильно это правильно писать в отчете",
     "Нужно правильно это писать в отчёте.", "re-cased and re-inflected"),
])
def test_legitimate_edits_pass(said, cleaned, why):
    """Every edit the cleanup is *supposed* to make, including Russian
    inflection and the ё that Whisper drops: the guard compares stems so a
    changed ending does not read as invention."""
    assert cleaner._no_invented_content(said, cleaned) is True, why


def test_guard_ignores_a_short_answer():
    """An empty or near-empty rewrite is the other guard's problem; this one
    must not double-report it."""
    assert cleaner._no_invented_content("раз два три четыре", "") is True


def test_the_fit_ignores_a_different_cleanup_model(monkeypatch):
    """Switching model changes the cost outright: measured here, gemma3:4b at
    1141 ms against qwen2.5:3b at 656 ms on the same sentence. Fitting across
    the change describes neither."""
    from fortunevoice import config

    config.set("FVOllamaModel", "qwen2.5:3b")
    stale = _rows([(chars, 3000 + chars * 9.0) for chars in range(20, 200, 10)],
                  model="gemma3:4b")
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(stale), raising=False)
    assert cleaner._fit_from_metrics() is None, "another model's runs are not data"


def test_the_fit_ignores_cold_loads_and_chunked_runs(monkeypatch):
    """A cold load is ~2 s whatever the text, and a chunked run's cost belongs
    to the flagged sentences rather than the whole transcript's length. Both
    were being fitted as per-character costs."""
    rows = (_rows([(30, 2016)], cleanup_cold=True)
            + _rows([(642, 437)], cleanup_chunks=1))
    monkeypatch.setattr(cleaner, "metrics", _FakeMetrics(rows * 8), raising=False)
    assert cleaner._fit_from_metrics() is None
