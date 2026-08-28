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

@pytest.fixture(autouse=True)
def no_real_model_download(request, monkeypatch):
    """A test run must not reach the network.

    Whisper is loaded from a background thread, and with nothing cached that
    is a multi-gigabyte download from Hugging Face. It surfaced as a stray SSL
    error in the middle of an otherwise green suite — reported under whichever
    test happened to be running when the leaked thread got there — and on a
    machine with no cached model it would be a test run that hangs for
    minutes.

    test_transcriber is the exception: it is *about* this code path, and stubs
    the loader itself.
    """
    if request.module.__name__.endswith("test_transcriber"):
        return
    from fortunevoice.transcriber import Transcriber, TranscriberError

    def refuse(self):
        raise TranscriberError("model loading is disabled in tests")

    monkeypatch.setattr(Transcriber, "_load_locked", refuse)
