"""Every module imports.

This is the cheapest test in the suite and it exists because of a real escape:
a bad edit merged two import lines in `app.py`, producing
`from .strings import t, RecoveryStore`. The app would have died on launch,
and 125 tests stayed green, because nothing in the suite imported `app` at
all. The biggest module in the project, the one holding the state machine, was
not even loaded.

Import errors, syntax errors, a renamed constant, a circular import: this
catches all of them in under a second, for every module, including the ones
with no unit tests of their own.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

import pytest

import fortunevoice

# The UI package needs a display only to *run*, not to import: the Tk objects
# are all created inside functions.
MODULES = sorted(
    name for _finder, name, _ispkg in pkgutil.walk_packages(
        fortunevoice.__path__, prefix="fortunevoice."
    )
)


def test_the_package_has_modules_to_check():
    """A collection bug that found nothing would make this file a no-op that
    still reports success."""
    assert len(MODULES) >= 20, MODULES


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only modules")
@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only modules")
def test_app_constructs_without_starting():
    """Constructing App wires the recorder callbacks and builds the stores.

    Nothing here touches the hotkey hook, the model or the UI: `start()` does
    that, so this stays a unit test while still covering the __init__ that
    every dictation depends on.
    """
    from fortunevoice.app import App, State

    app = App()
    assert app.state is State.LOADING
    assert app.recorder.on_level is not None
    assert app.hotkey_label  # falls back to the configured string before start()
