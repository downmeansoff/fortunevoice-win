"""The palette is referenced by name, so a rename is a silent break.

Split out of the UI smoke tests on purpose: those need a desktop session and
are opt-in, while this is pure text analysis and must run on every commit.

It exists because of a real failure — rewriting the palette removed
`theme.INK_RAISED` and two windows still referenced it. The main window
screenshotted perfectly while the first-run screen raised AttributeError
inside a UI callback, was swallowed by the event pump, and simply never
appeared.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_no_dangling_theme_attributes():
    """Static check, so it runs even without a display.

    The palette is a plain module of constants and the windows reference it by
    attribute; renaming one is a silent break until the window is opened.
    """
    from fortunevoice.ui import theme

    ui_dir = Path(__file__).resolve().parents[1] / "src" / "fortunevoice" / "ui"
    used: set[str] = set()
    for path in ui_dir.glob("*.py"):
        used |= set(re.findall(r"theme\.([A-Za-z_][A-Za-z0-9_]*)",
                              path.read_text(encoding="utf-8")))
    missing = sorted(name for name in used if not hasattr(theme, name))
    assert not missing, f"ui/ references theme attributes that do not exist: {missing}"
