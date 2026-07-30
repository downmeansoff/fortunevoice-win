"""Create the Desktop shortcut (and optionally a startup one).

    python scripts/install_shortcut.py
    python scripts/install_shortcut.py --startup     # also launch at login
    python scripts/install_shortcut.py --remove

The work lives in `fortunevoice.shortcut` because the Settings window has a
"Launch at login" switch that is exactly "is there a shortcut in the Startup
folder"; this is the command-line way in.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fortunevoice import shortcut  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup", action="store_true",
                        help="also add a shortcut to the Startup folder")
    parser.add_argument("--remove", action="store_true", help="delete both shortcuts")
    args = parser.parse_args()

    if args.remove:
        for folder in (shortcut.desktop(), shortcut.startup()):
            if shortcut.remove(folder):
                print(f"removed {folder / shortcut.NAME}")
        return 0

    print(f"created {shortcut.create(shortcut.desktop(), 'Local voice dictation - hold a hotkey and speak')}")
    if args.startup:
        print(f"created {shortcut.create(shortcut.startup(), 'FortuneVoice (starts with Windows)')}")
    print(f"\nlaunching: {shortcut.interpreter()} -m fortunevoice")
    print(f"from:      {shortcut.ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
