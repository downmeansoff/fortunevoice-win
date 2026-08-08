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


def test_an_unreadable_file_does_not_erase_the_other_settings():
    """Reported as "my shortcut keeps going back to ctrl+alt+space".

    `set()` rewrites the whole file from what it believes the settings are.
    After a failed read that belief used to be DEFAULTS alone — so one corrupt
    or briefly locked file, plus any later change, silently reset every setting
    the user had made. ctrl+alt+space is simply the default hotkey, which is
    why that is the value they saw come back.
    """
    config.set("FVHotkey", "ctrl+alt")
    config.set("FVLanguage", "en")

    paths.config_file().write_text("{ truncated", encoding="utf-8")
    config._cache_mtime = None  # noqa: SLF001 - force a reload
    config.set("FVMiniPrompt", False)  # any unrelated change at all

    assert config.get_str("FVHotkey") == "ctrl+alt"
    assert config.get_str("FVLanguage") == "en"
    assert config.get_bool("FVMiniPrompt") is False


def test_the_unreadable_file_is_kept_rather_than_overwritten():
    """It may hold keys written by a newer build; overwriting it in place
    would leave no copy at all."""
    config.set("FVHotkey", "ctrl+alt")
    paths.config_file().write_text("{ truncated", encoding="utf-8")
    config._cache_mtime = None  # noqa: SLF001
    config.set("FVLanguage", "en")

    kept = paths.config_file().with_suffix(".json.corrupt")
    assert kept.exists()
    assert kept.read_text(encoding="utf-8") == "{ truncated"


def test_toggle():
    before = config.get_bool("FVCleanupEnabled")
    assert config.toggle("FVCleanupEnabled") is (not before)
    assert config.get_bool("FVCleanupEnabled") is (not before)


def test_a_zero_length_config_does_not_erase_the_settings():
    """What an abrupt power-off leaves behind: the rename went through, the
    contents never reached the platter. `set()` now fsyncs before renaming so
    this should not happen — but if it does, the settings must still survive.
    """
    config.set("FVHotkey", "ctrl+alt")
    paths.config_file().write_text("", encoding="utf-8")
    config._cache_mtime = None  # noqa: SLF001
    config.set("FVLanguage", "en")

    assert config.get_str("FVHotkey") == "ctrl+alt"


def test_the_write_is_flushed_before_the_rename():
    """Without the fsync the window for the empty-file case above stays open
    on every single write."""
    import inspect

    source = inspect.getsource(config.set)
    assert "os.fsync" in source
    assert source.index("os.fsync") < source.index("tmp.replace")
