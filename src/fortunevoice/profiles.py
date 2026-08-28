"""Per-application overrides.

Dictating a shell command into a terminal and dictating a message into a chat
want opposite things. Cleanup fixing punctuation and dropping filler words is
right for the message and actively wrong for `git rebase -i HEAD~3`, which it
will happily capitalise and punctuate into something that does not run.

Stored in config.json under FVAppProfiles, keyed by executable name:

    "FVAppProfiles": {
      "WindowsTerminal.exe": {"FVCleanupEnabled": false},
      "Telegram.exe": {"FVVoiceCommands": true}
    }

Deliberately not a UI. It is a rarely-changed mapping, editable in the file the
tray already opens, and a window for it would be more code than the feature.

Only settings in OVERRIDABLE can be overridden. A profile that could change
anything would be a config file able to point the app at a different Ollama
host or a different microphone depending on which window is in front —
surprising in the specific way that makes a bug impossible to find.
"""

from __future__ import annotations

from . import config
from .log import get as get_logger

logger = get_logger("profiles")

# Per-dictation behaviour, and nothing else. Not hosts, not paths, not the
# hotkey — those are properties of the installation, not of the window that
# happens to be in front.
OVERRIDABLE = frozenset({
    "FVCleanupEnabled",
    "FVSmartFix",
    "FVVoiceCommands",
    "FVStreaming",
    "FVPasteViaClipboard",
})
# Not FVMiniPrompt. It is read inside the cleaner, on a worker thread, after
# the user has already let go and may well have switched windows — so "the app
# in front" is no longer a meaningful question by then. It was listed here and
# read straight from config, which is the worst of both: a documented override
# that silently did nothing.


def _table() -> dict[str, dict]:
    raw = config.get("FVAppProfiles")
    return raw if isinstance(raw, dict) else {}


def overrides_for(app: str | None) -> dict:
    """The overrides that apply to `app`, matched without case.

    Windows reports executable names inconsistently between APIs and versions
    (`Code.exe` and `code.exe` are the same editor), so a profile that only
    matched exactly would work until it silently did not.
    """
    if not app:
        return {}
    wanted = app.lower()
    for name, values in _table().items():
        if str(name).lower() != wanted or not isinstance(values, dict):
            continue
        allowed = {k: v for k, v in values.items() if k in OVERRIDABLE}
        ignored = set(values) - set(allowed)
        if ignored:
            logger.warning("profile for %s ignores %s — not overridable",
                           app, ", ".join(sorted(ignored)))
        return allowed
    return {}


def get_bool(key: str, app: str | None) -> bool:
    """`config.get_bool`, with the profile for `app` on top."""
    overrides = overrides_for(app)
    if key in overrides:
        return bool(overrides[key])
    return config.get_bool(key)
