"""Per-dictation timings, appended as JSON Lines.

Every tuning decision carried over from the macOS build — the cleanup cost
model, the streaming defaults, the tail-capture length — came from reading
this file, not from a benchmark. Keeping the same fields means the two ports'
numbers stay directly comparable.

    %APPDATA%\\FortuneVoice\\metrics.jsonl
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime

from . import paths
from .log import get as get_logger

logger = get_logger("metrics")

_lock = threading.Lock()
# A dictation a minute for a year is ~130k lines; well under what a JSONL file
# handles comfortably, but not unbounded.
MAX_LINES = 200_000


@dataclass
class DictationMetric:
    date: str
    capture_sec: float
    stt_ms: float
    cleanup_ms: float
    total_ms: float
    chars: int
    outcome: str
    cleanup_skipped: bool
    retried: bool
    stream_passes: int
    logprob: float
    model: str
    device: str | None = None
    cleanup_chunks: int | None = None
    cleanup_skipped_sentences: int | None = None
    shadow_diff_words: int | None = None
    stitched_ms: float | None = None
    batch_ms: float | None = None
    pre_cleaned_words: int | None = None
    cleanup_over_budget: int | None = None


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ends_with_a_newline(path) -> bool:
    """Did the last write finish?

    A machine losing power mid-append leaves a line without its terminator.
    Appending straight onto that glues the next dictation's metric to the
    broken one and loses them BOTH — the damage spreading one record past
    where it happened. Costs one byte to check, not a read of the file.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(-1, 2)  # SEEK_END
            return handle.read(1) == b"\n"
    except OSError:
        return True  # missing or empty — nothing to repair


def record(metric: DictationMetric) -> None:
    line = json.dumps(asdict(metric), ensure_ascii=False)
    path = paths.metrics_file()
    with _lock:
        try:
            prefix = "" if _ends_with_a_newline(path) else "\n"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(prefix + line + "\n")
        except OSError as exc:
            logger.warning("could not write metrics: %s", exc)
            return
        _trim(path)


def _trim(path) -> None:
    try:
        if path.stat().st_size < 30_000_000:
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= MAX_LINES:
            return
        path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def read_all() -> list[dict]:
    try:
        raw = paths.metrics_file().read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
