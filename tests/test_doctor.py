"""The self-check, and the stats summary.

This is what the user runs when dictation is not working. A doctor that lies —
reporting fine when it is not, or hiding one failure behind another — leaves
them worse off than no doctor at all, because now they trust it.

Nothing real is touched: no model is loaded, no microphone opened, no HTTP.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice import config, doctor, metrics  # noqa: E402


def run_with(monkeypatch, checks):
    """Drive the real `run()` over the given checks."""
    import types

    source = doctor.run.__code__
    namespace = dict(doctor.run.__globals__)
    for name, check in checks.items():
        namespace[name] = check
    rebuilt = types.FunctionType(source, namespace, "run")
    return rebuilt()


def passing(_=None):
    return True


def failing(_=None):
    return False


# ── the exit code is the whole contract for a script ─────────────────────


def test_all_checks_passing_exits_zero(monkeypatch, capsys):
    code = run_with(monkeypatch, {name: passing for name in
                                  ("_check_hotkey", "_check_audio", "_check_injection",
                                   "_check_model", "_check_ollama", "_check_vram")})
    assert code == 0
    assert "All checks passed" in capsys.readouterr().out


def test_one_failure_exits_non_zero(monkeypatch, capsys):
    checks = {name: passing for name in
              ("_check_hotkey", "_check_audio", "_check_injection",
               "_check_model", "_check_ollama", "_check_vram")}
    checks["_check_model"] = failing
    code = run_with(monkeypatch, checks)
    assert code == 1
    assert "1 check(s) failed" in capsys.readouterr().out


def test_a_check_that_raises_is_reported_and_counted(monkeypatch, capsys):
    """One broken check must not hide the rest — that is the failure mode that
    makes a diagnostic useless exactly when it is needed."""
    def explode():
        raise RuntimeError("the GPU query blew up")

    checks = {name: passing for name in
              ("_check_hotkey", "_check_audio", "_check_injection",
               "_check_model", "_check_ollama", "_check_vram")}
    checks["_check_vram"] = explode
    code = run_with(monkeypatch, checks)
    out = capsys.readouterr().out
    assert code == 1
    assert "the GPU query blew up" in out


def test_every_check_runs_even_after_one_fails(monkeypatch, capsys):
    """Stopping at the first failure would make the user fix one thing, run
    again, fix the next — instead of seeing the whole picture at once."""
    ran: list[str] = []
    checks = {}
    for name in ("_check_hotkey", "_check_audio", "_check_injection",
                 "_check_model", "_check_ollama", "_check_vram"):
        checks[name] = (lambda n=name: ran.append(n) or False)
    code = run_with(monkeypatch, checks)
    assert len(ran) == 6, "every check ran"
    assert code == 1, "the exit code is a yes/no, not a count"
    assert "6 check(s) failed" in capsys.readouterr().out


# ── the hotkey check ─────────────────────────────────────────────────────


def test_a_good_hotkey_passes_and_prints_its_label(capsys):
    config.set("FVHotkey", "ctrl+alt")
    assert doctor._check_hotkey() is True
    assert "Ctrl+Alt" in capsys.readouterr().out


def test_a_broken_hotkey_fails_and_says_where_to_fix_it(capsys):
    """"It doesn't type anything" comes down to this more often than to
    anything else, and the file to edit is not obvious."""
    config.set("FVHotkey", "ctrl+spacebar")
    assert doctor._check_hotkey() is False
    out = capsys.readouterr().out
    assert "config.json" in out
    assert "unknown key" in out


# ── the stats summary ────────────────────────────────────────────────────


def a_row(**overrides):
    fields = dict(date=metrics.now(), capture_sec=2.0, stt_ms=400.0,
                  cleanup_ms=500.0, total_ms=800.0, chars=20, outcome="pasted",
                  cleanup_skipped=False, retried=False, stream_passes=1,
                  logprob=-0.2, model="large-v3-turbo")
    fields.update(overrides)
    return metrics.DictationMetric(**fields)


def test_stats_on_an_empty_history_says_so(capsys):
    assert doctor.stats() == 0
    assert "No dictations recorded yet" in capsys.readouterr().out


def test_the_median_is_a_median_not_a_mean(capsys):
    """One 10-second outlier must not move the number the tuning decisions are
    read off."""
    for total in (100.0, 200.0, 10_000.0):
        metrics.record(a_row(total_ms=total))
    doctor.stats()
    assert "200 ms" in capsys.readouterr().out


def test_an_even_count_averages_the_middle_two(capsys):
    for total in (100.0, 200.0, 300.0, 400.0):
        metrics.record(a_row(total_ms=total))
    doctor.stats()
    assert "250 ms" in capsys.readouterr().out


def test_skipped_cleanups_do_not_drag_the_cleanup_median_down(capsys):
    """A skipped cleanup took 0 ms because it never happened. Counting those
    would report cleanup as far faster than it is."""
    metrics.record(a_row(cleanup_ms=600.0, cleanup_skipped=False))
    for _ in range(5):
        metrics.record(a_row(cleanup_ms=0.0, cleanup_skipped=True))
    doctor.stats()
    assert "600 ms  (1 runs)" in capsys.readouterr().out


def test_typed_counts_both_confirmed_and_blind(capsys):
    """Most deliveries are "blind" — Windows would not confirm an editable
    field. Counting only the confirmed ones would report the app as barely
    working."""
    metrics.record(a_row(outcome="pasted"))
    metrics.record(a_row(outcome="pasted-blind"))
    metrics.record(a_row(outcome="panel-focus"))
    doctor.stats()
    assert "3 dictations, 2 typed into an app" in capsys.readouterr().out


def test_outcomes_are_listed_commonest_first(capsys):
    for _ in range(3):
        metrics.record(a_row(outcome="pasted-blind"))
    metrics.record(a_row(outcome="silence"))
    doctor.stats()
    out = capsys.readouterr().out
    assert out.index("pasted-blind") < out.index("silence")


# ── the cleanup model check ──────────────────────────────────────────────


def _ollama_report(monkeypatch, capsys, installed):
    """Run doctor's Ollama check against a fake `/api/tags`."""
    import json
    import urllib.request

    from fortunevoice import ollama as ollama_app

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps(
                {"models": [{"name": name} for name in installed]}).encode("utf-8")

    monkeypatch.setattr(ollama_app, "ensure_running", lambda: True)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Response())
    config.set("FVCleanupEnabled", True)
    doctor._check_ollama()
    return capsys.readouterr().out


def test_a_different_size_of_the_same_family_is_not_the_model(monkeypatch, capsys):
    """Matching on the family name alone reported "qwen2.5:3b available" when
    what was installed was qwen2.5:1.5b. Cleanup then asked Ollama for a model
    it does not have, got a 404 and fell back to raw text — with doctor still
    showing a tick."""
    config.set("FVOllamaModel", "qwen2.5:3b")
    assert "not installed" in _ollama_report(monkeypatch, capsys, ["qwen2.5:1.5b"])


def test_the_wanted_model_is_recognised(monkeypatch, capsys):
    config.set("FVOllamaModel", "qwen2.5:3b")
    assert "available" in _ollama_report(monkeypatch, capsys, ["qwen2.5:3b"])


def test_ollamas_implicit_latest_tag_still_matches(monkeypatch, capsys):
    """`ollama list` reports a tagless pull as "name:latest"."""
    config.set("FVOllamaModel", "gemma3")
    assert "available" in _ollama_report(monkeypatch, capsys, ["gemma3:latest"])
