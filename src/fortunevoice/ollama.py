"""Finding Ollama, and starting it when it is not there.

Ollama on Windows does not stay up. The desktop app exits, the server exits
with it, and nothing tells the user — so cleanup silently stops happening and
every dictation comes back as raw Whisper text. It looks exactly like the
feature being switched off, and the only trace is a line in the log.

The app is installed and, on this machine, in the user's Startup folder: it is
meant to be running. So rather than degrading quietly, `ensure_running()`
starts it and waits for the port.

Deliberately narrow:

* Only ever starts a binary found in the places Ollama's own installer puts
  it, or on PATH. Nothing configurable, nothing from the config file — a
  setting that names an executable to launch is a setting worth attacking.
* Never starts anything when the port already answers.
* One attempt at a time, and a short cooldown after a failure. A user with no
  Ollama installed must not pay a spawn attempt on every dictation.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

# Where Ollama's installer puts things. Per-user first: that is the default
# install, and the one that will be on the user's PATH.
_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama app.exe",
    Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
)

# How long to wait for the port after starting the app. Measured cold on this
# machine: 13-15 s when nothing else was starting, and 25-28 s when Ollama and
# FortuneVoice came up together and competed for the disk. 12 s and then 25 s
# both timed out on a server that was coming up fine, and the second one was
# bad enough to fire a "cleanup does not work" notification at a user whose
# cleanup started working seconds later. Waiting costs nothing real — the only
# callers block a daemon thread — so the budget is generous on purpose.
STARTUP_TIMEOUT = 60.0
# Not retried more often than this after a failure, so a machine without
# Ollama does not pay a spawn attempt per dictation.
COOLDOWN = 120.0

_lock = threading.Lock()
_last_failure = 0.0


def executable() -> Path | None:
    """The Ollama binary, or None if it is not installed.

    `ollama app.exe` is preferred over `ollama.exe`: it is the tray app, which
    is what the user's own Startup shortcut runs, and it manages the server's
    lifetime. Starting `ollama.exe serve` instead would leave a second server
    fighting the first one for the port the next time the app comes up.
    """
    for path in _CANDIDATES:
        if path.parent.name and path.is_file():
            return path
    # PATH, but never the current directory. `shutil.which` on Windows
    # searches "." first by default, so a file called ollama.exe sitting in
    # whatever folder the app happened to start in would be launched — and
    # this module's own docstring promises it only ever starts a binary from
    # where Ollama's installer puts it.
    found = shutil.which("ollama", path=os.environ.get("PATH", ""))
    if not found:
        return None
    resolved = Path(found).resolve()
    if resolved.parent == Path.cwd().resolve():
        logger.warning("ignoring %s — it is in the working directory", resolved)
        return None
    return resolved


def is_up(timeout: float = 1.0) -> bool:
    host = config.get_str("FVOllamaHost").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_running(wait: float = STARTUP_TIMEOUT) -> bool:
    """True if Ollama answers — starting it first if it does not.

    Safe to call from any thread and as often as you like: the fast path is a
    single HTTP request to a local port, and only one caller at a time gets to
    start anything.
    """
    global _last_failure

    if is_up():
        return True
    if not config.get_bool("FVAutoStartOllama"):
        return False
    with _lock:
        if is_up():  # started while we waited for the lock
            return True
        if time.monotonic() - _last_failure < COOLDOWN:
            return False
        binary = executable()
        if binary is None:
            logger.info("Ollama is not installed — cleanup will be skipped")
            _last_failure = time.monotonic()
            return False

        logger.info("Ollama is not running, starting %s", binary.name)
        try:
            # Detached, and with no window: this is the user's tray app, not a
            # child whose lifetime should follow ours. DETACHED_PROCESS alone
            # is not enough — without a new process group, our own Ctrl-C
            # would reach it.
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(  # noqa: S603 - fixed path, no user input
                [str(binary)], creationflags=flags,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, close_fds=True,
            )
        except OSError:
            logger.warning("could not start Ollama", exc_info=True)
            _last_failure = time.monotonic()
            return False

        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if is_up():
                logger.info("Ollama answered after %.1f s",
                            wait - (deadline - time.monotonic()))
                return True
            time.sleep(0.4)
        logger.warning("Ollama did not answer within %.0f s", wait)
        _last_failure = time.monotonic()
        return False
