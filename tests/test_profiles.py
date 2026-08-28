"""Per-application overrides.

Dictating a shell command and dictating a chat message want opposite things:
cleanup punctuating a message is right, and cleanup punctuating
`git rebase -i HEAD~3` into a sentence produces something that does not run.
"""

from __future__ import annotations

from fortunevoice import config, profiles


def test_no_profiles_means_the_global_setting():
    config.set("FVCleanupEnabled", True)
    assert profiles.get_bool("FVCleanupEnabled", "Code.exe") is True


def test_a_profile_overrides_it():
    config.set("FVCleanupEnabled", True)
    config.set("FVAppProfiles", {"WindowsTerminal.exe": {"FVCleanupEnabled": False}})
    assert profiles.get_bool("FVCleanupEnabled", "WindowsTerminal.exe") is False
    assert profiles.get_bool("FVCleanupEnabled", "Telegram.exe") is True


def test_matching_ignores_case():
    """Windows reports executable names inconsistently between APIs and
    versions; a profile that only matched exactly would work until it did not."""
    config.set("FVAppProfiles", {"Code.exe": {"FVCleanupEnabled": False}})
    assert profiles.get_bool("FVCleanupEnabled", "code.exe") is False
    assert profiles.get_bool("FVCleanupEnabled", "CODE.EXE") is False


def test_an_unknown_app_falls_through():
    config.set("FVCleanupEnabled", True)
    config.set("FVAppProfiles", {"Code.exe": {"FVCleanupEnabled": False}})
    assert profiles.get_bool("FVCleanupEnabled", None) is True
    assert profiles.get_bool("FVCleanupEnabled", "") is True


def test_only_per_dictation_settings_can_be_overridden():
    """A profile able to change anything would be a config file pointing the
    app at a different Ollama host depending on which window is in front —
    surprising in the specific way that makes a bug impossible to find."""
    config.set("FVAppProfiles", {"Code.exe": {
        "FVCleanupEnabled": False,      # allowed
        "FVOllamaHost": "http://evil",  # not
        "FVHotkey": "f1",               # not
    }})
    overrides = profiles.overrides_for("Code.exe")
    assert overrides == {"FVCleanupEnabled": False}


def test_a_malformed_table_is_ignored():
    """The file is meant to be hand-edited, so it will be hand-broken."""
    for junk in ("not a dict", 42, None, ["a", "b"]):
        config.set("FVAppProfiles", junk)
        assert profiles.overrides_for("Code.exe") == {}

    config.set("FVAppProfiles", {"Code.exe": "not a dict either"})
    assert profiles.overrides_for("Code.exe") == {}


def test_every_overridable_key_is_a_real_setting():
    """A typo here would be a setting that silently never applies."""
    for key in profiles.OVERRIDABLE:
        assert key in config.DEFAULTS, key


def test_the_name_the_app_looks_up_is_the_name_profiles_are_keyed_by(monkeypatch):
    """The unit tests above always passed. What did not work: every caller
    passed `winapi.foreground_app_name()`, which returned the window TITLE, so
    a profile keyed "WindowsTerminal.exe" was compared against "gleb@pc: ~"
    and never matched anything. The feature was documented, configurable and
    dead."""
    from fortunevoice import winapi

    monkeypatch.setattr(winapi, "process_name", lambda hwnd: "WindowsTerminal.exe")
    monkeypatch.setattr(winapi, "window_title", lambda hwnd: "gleb@pc: ~")
    config.set("FVCleanupEnabled", True)
    config.set("FVAppProfiles", {"WindowsTerminal.exe": {"FVCleanupEnabled": False}})

    assert profiles.get_bool("FVCleanupEnabled", winapi.foreground_app_name()) is False
