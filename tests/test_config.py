"""Settings file behaviour. A corrupt or hand-edited config must never stop
the app from dictating."""

from fortunevoice import config, paths


def test_defaults_when_no_file():
    assert config.get_str("FVHotkey") == "ctrl+alt+space"
    assert config.get_bool("FVStreaming") is True


def test_set_persists_and_reloads():
    config.set("FVLanguage", "en")
    assert config.get_str("FVLanguage") == "en"
    assert "FVLanguage" in paths.config_file().read_text(encoding="utf-8")


def test_only_non_defaults_are_written():
    """A value the user never touched must not be frozen into their config —
    otherwise a future default change would never reach them."""
    config.set("FVLanguage", "en")
    stored = paths.config_file().read_text(encoding="utf-8")
    assert "FVOllamaModel" not in stored


def test_setting_back_to_default_removes_it():
    config.set("FVLanguage", "en")
    config.set("FVLanguage", config.DEFAULTS["FVLanguage"])
    assert "FVLanguage" not in paths.config_file().read_text(encoding="utf-8")


def test_corrupt_config_falls_back_to_defaults():
    paths.config_file().write_text("{{{ not json", encoding="utf-8")
    config._cache_mtime = None  # noqa: SLF001 - force a reload
    assert config.get_str("FVHotkey") == "ctrl+alt+space"


def test_toggle():
    before = config.get_bool("FVCleanupEnabled")
    assert config.toggle("FVCleanupEnabled") is (not before)
    assert config.get_bool("FVCleanupEnabled") is (not before)
