import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the user's real %APPDATA%\\FortuneVoice during a test run."""
    monkeypatch.setenv("FORTUNEVOICE_HOME", str(tmp_path / "home"))
    from fortunevoice import config

    monkeypatch.setattr(config, "_cache", {}, raising=False)
    monkeypatch.setattr(config, "_cache_mtime", None, raising=False)
    yield
    os.environ.pop("FORTUNEVOICE_HOME", None)


@pytest.fixture(autouse=True)
def no_real_ollama(monkeypatch):
    """A test run must not launch Ollama.

    `cleaner.warmup()` starts it when the port is quiet, and the startup
    budget is 60 s — so the moment warmup appeared in a test the suite went
    from 6 s to 65 s and left a real Ollama process behind. Reporting "not
    installed" makes `ensure_running()` give up at once.

    Tests that are specifically about starting it override this with their own
    monkeypatch, which is applied after this fixture and therefore wins.
    """
    from fortunevoice import ollama

    monkeypatch.setattr(ollama, "executable", lambda: None)
