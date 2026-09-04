"""Starting Ollama when it is down.

The module spawns a process, so the rules that matter are about when it does
NOT: when the port already answers, when the setting is off, when nothing is
installed, and repeatedly after a failure.
"""

from __future__ import annotations

import pytest

from fortunevoice import config, ollama


@pytest.fixture(autouse=True)
def _reset():
    ollama._last_failure = 0.0  # noqa: SLF001 - cooldown is module state
    yield
    ollama._last_failure = 0.0  # noqa: SLF001


def _never_spawn(monkeypatch):
    """Fail the test if anything is launched."""
    def boom(*args, **kwargs):
        raise AssertionError(f"should not have started a process: {args}")

    monkeypatch.setattr(ollama.subprocess, "Popen", boom)


def test_no_spawn_when_already_up(monkeypatch):
    monkeypatch.setattr(ollama, "is_up", lambda timeout=1.0: True)
    _never_spawn(monkeypatch)
    assert ollama.ensure_running() is True


def test_no_spawn_when_the_setting_is_off(monkeypatch):
    monkeypatch.setattr(ollama, "is_up", lambda timeout=1.0: False)
    _never_spawn(monkeypatch)
    config.set("FVAutoStartOllama", False)
    assert ollama.ensure_running() is False


def test_no_spawn_when_ollama_is_not_installed(monkeypatch):
    monkeypatch.setattr(ollama, "is_up", lambda timeout=1.0: False)
    monkeypatch.setattr(ollama, "executable", lambda: None)
    _never_spawn(monkeypatch)
    assert ollama.ensure_running() is False


def test_a_failure_is_not_retried_immediately(monkeypatch, tmp_path):
    """A machine without a working Ollama must not pay a spawn attempt on
    every dictation: warmup runs on every hotkey press."""
    binary = tmp_path / "ollama app.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(ollama, "is_up", lambda timeout=1.0: False)
    monkeypatch.setattr(ollama, "executable", lambda: binary)

    attempts = []
    monkeypatch.setattr(ollama.subprocess, "Popen",
                        lambda *a, **k: attempts.append(a))
    assert ollama.ensure_running(wait=0.0) is False
    assert ollama.ensure_running(wait=0.0) is False
    assert len(attempts) == 1, "the second call must be inside the cooldown"


def test_it_waits_for_the_port_rather_than_assuming(monkeypatch, tmp_path):
    """A cold tray app took 13-15 s here. Returning as soon as the process
    exists would report success while nothing is listening."""
    binary = tmp_path / "ollama app.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(ollama, "executable", lambda: binary)
    monkeypatch.setattr(ollama.subprocess, "Popen", lambda *a, **k: None)

    answers = iter([False, False, False, True])
    monkeypatch.setattr(ollama, "is_up",
                        lambda timeout=1.0: next(answers, True))
    assert ollama.ensure_running(wait=10.0) is True


def test_the_startup_timeout_covers_a_measured_cold_start():
    """12 s used to time out on a server that was coming up fine."""
    assert ollama.STARTUP_TIMEOUT >= 20.0
