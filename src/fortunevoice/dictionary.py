"""Custom vocabulary — names and jargon Whisper keeps mishearing.

Stored as a plain JSON list the user can edit by hand:

    %APPDATA%\\FortuneVoice\\dictionary.json   →   ["Fortune VPN", "Xray", …]

Fed to Whisper as an initial prompt (biases the decoder) and to the cleanup
model as "prefer these terms".
"""

from __future__ import annotations

import json

from . import paths

# Whisper's prompt window is small and every prompt token is decoded on every
# 30 s chunk. The macOS build capped the tokenized prompt at 200 tokens; we cap
# the source string instead, which is the same guard one layer earlier.
MAX_PROMPT_CHARS = 600


def terms() -> list[str]:
    path = paths.dictionary_file()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def set_terms(values: list[str]) -> None:
    path = paths.dictionary_file()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def prompt_string() -> str:
    joined = ", ".join(terms())
    return joined[:MAX_PROMPT_CHARS]
