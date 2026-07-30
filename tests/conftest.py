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
