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
# A record is ~515 bytes, so this is ~10 MB — comfortably under the 30 MB at
# which the trim starts, which is the point. At 200_000 the two gates
# disagreed: the file had to reach 30 MB before anything happened, and then
# the line cap kept every line, so nothing was ever removed. From there on
# every dictation read and split a 30 MB file, under the lock, after the text
# had already been typed — and the file went on growing towards 100 MB.
# 20_000 dictations is a year of heavy use, and the cost model only reads the
# recent ones.
MAX_LINES = 20_000


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
    # Which model did the cleaning, and whether it had to be loaded first.
    # Without these the cost fit pooled runs that are not comparable: `model`
    # above is the WHISPER model, so switching the cleanup model — gemma3:4b at
    # 1141 ms against qwen2.5:3b at 656 ms on the same text — was invisible to
    # it, and a cold load (~2 s regardless of length) looked like a very
    # expensive short dictation.
    cleanup_model: str | None = None
    cleanup_cold: bool | None = None


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
        # errors="replace", not a wider except: a machine that lost power
        # mid-append leaves a Cyrillic character cut in half, and one bad byte
        # then raised UnicodeDecodeError out of every reader. Replacing keeps
        # the damage inside the one line it belongs to; that line fails the
        # per-line json parse and is skipped, and every intact record around
        # it survives.
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= MAX_LINES:
            return
        # Through a temp file, like the history store: a write interrupted
        # here would otherwise leave the live file truncated, which is the
        # very corruption this function exists to bound.
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def read_all() -> list[dict]:
    try:
        # See _trim: one byte of a half-written character used to throw
        # UnicodeDecodeError — which is a ValueError, not an OSError — straight
        # out of the Insights page and out of `doctor stats`, permanently,
        # until the user found and hand-edited the file.
        raw = paths.metrics_file().read_text(encoding="utf-8", errors="replace")
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
