"""Settings file behaviour. A corrupt or hand-edited config must never stop
the app from dictating."""

from pathlib import Path

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


def test_the_write_is_flushed_before_the_rename(monkeypatch):
    """Without the fsync, the window for the empty-file case above stays open
    on every single write.

    Checked by watching the calls, not by reading the source: an assertion
    that "os.fsync" appears in the text of `set` passes just as happily when
    the fsync is on the wrong handle, inside a branch that never runs, or in a
    comment.
    """
    import os as os_module

    order: list[str] = []
    real_fsync = os_module.fsync
    real_replace = Path.replace

    def spy_fsync(fd):
        order.append("fsync")
        return real_fsync(fd)

    def spy_replace(self, target):
        # Only the config write, not the .corrupt rename beside it.
        if str(self).endswith(".tmp"):
            order.append("replace")
        return real_replace(self, target)

    monkeypatch.setattr(os_module, "fsync", spy_fsync)
    monkeypatch.setattr(Path, "replace", spy_replace)

    config.set("FVLanguage", "ru")

    assert order == ["fsync", "replace"], order


def test_two_threads_writing_different_settings_keep_both():
    """`set()` is a read-modify-write of the whole file. Without a lock across
    it, two threads each read, change their own key and write the lot back —
    and the second write silently drops the first one's change. Not
    theoretical: the tray sets the microphone, the window saves its geometry as
    it closes, and the app writes FVOnboarded, all from different threads."""
    import threading

    config.set("FVHotkey", "ctrl+alt")
    start = threading.Barrier(2)
    pairs = [("FVLanguage", "en"), ("FVMicrophone", "Mic (MCHOSE V9 PRO)")]

    def writer(key, value):
        start.wait(5)
        for _ in range(40):
            config.set(key, value)

    threads = [threading.Thread(target=writer, args=pair) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)

    config._cache_mtime = None  # noqa: SLF001 - read what is on disk
    assert config.get_str("FVLanguage") == "en"
    assert config.get_str("FVMicrophone") == "Mic (MCHOSE V9 PRO)"
    assert config.get_str("FVHotkey") == "ctrl+alt"
