"""Persistence: dictation history and the crash pad for failed decodes.

History is the vault. Delivery writes here FIRST, before anything that could
fail, so a transcript can never be lost to a stuck paste, a switched window or
a missing permission.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import paths
from .log import get as get_logger

logger = get_logger("store")

SAMPLE_RATE = 16_000


@dataclass
class DictationRecord:
    date: str
    words: int
    duration: float
    app: str | None
    transcript: str
    # The exact spoken words, kept only when cleanup changed them, so a
    # mis-edit by the LLM is always recoverable.
    raw: str | None = None


@dataclass
class DictationStats:
    count: int = 0
    words: int = 0
    seconds: float = 0.0
    # Words per minute of speaking time, over the records counted above.
    wpm: float = 0.0


class DictationStore:
    """Append-only history, capped and pruned. One JSON array on disk — a
    dictation log is thousands of rows at most, and a single readable file
    beats a database the user can't inspect."""

    MAX_RECORDS = 5_000

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.history_file()
        self._lock = threading.Lock()

    def all(self) -> list[DictationRecord]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        out: list[DictationRecord] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                out.append(
                    DictationRecord(
                        date=str(item.get("date", "")),
                        words=int(item.get("words", 0)),
                        duration=float(item.get("duration", 0.0)),
                        app=item.get("app"),
                        transcript=str(item.get("transcript", "")),
                        raw=item.get("raw"),
                    )
                )
            except (TypeError, ValueError):
                continue
        return out

    def add(self, record: DictationRecord) -> None:
        with self._lock:
            records = self.all()
            records.append(record)
            if len(records) > self.MAX_RECORDS:
                records = records[-self.MAX_RECORDS :]
            self._write(records)

    def remove(self, record: "DictationRecord") -> bool:
        """Delete one dictation, matched on timestamp and text.

        Matched on content rather than index because the caller is a UI list
        that was built from a filtered, reversed copy — an index into that is
        not an index into the file, and using one would delete a neighbour.
        """
        with self._lock:
            # Under the lock like every other mutation: a dictation landing
            # between the read and the write would be lost, and the vault
            # promise ("nothing is ever silently discarded") with it.
            records = self.all()
            for position, existing in enumerate(records):
                if (existing.date == record.date
                        and existing.transcript == record.transcript):
                    del records[position]
                    self._write(records)
                    return True
        return False

    def edit(self, record: "DictationRecord", transcript: str) -> bool:
        """Replace one dictation's text, keeping everything else about it.

        Matched the way `remove` matches, and for the same reason: the caller
        holds a row out of a filtered, reversed copy.

        `raw` is left alone, and filled in from the old text when it was empty.
        It is the exact spoken words, and it is what makes a bad edit — by the
        cleanup model or by the user — recoverable; an edit that overwrote it
        would be the one edit that cannot be undone.
        """
        transcript = transcript.strip()
        if not transcript or transcript == record.transcript:
            return False
        with self._lock:
            records = self.all()
            for position, existing in enumerate(records):
                if (existing.date == record.date
                        and existing.transcript == record.transcript):
                    records[position] = replace(
                        existing, transcript=transcript,
                        words=len(transcript.split()),
                        raw=existing.raw or existing.transcript,
                    )
                    self._write(records)
                    return True
        return False

    def clear(self) -> None:
        """Delete every record. Only reachable from an explicit confirmation in
        the UI — this is the vault every delivery path writes to first."""
        with self._lock:
            self._write([])

    def prune(self, older_than_days: int) -> None:
        if older_than_days <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        with self._lock:
            kept = []
            for record in self.all():
                try:
                    when = datetime.fromisoformat(record.date)
                except ValueError:
                    kept.append(record)  # unparseable date → keep, never guess
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when >= cutoff:
                    kept.append(record)
            self._write(kept)

    def stats(self, days: int | None = None) -> DictationStats:
        records = self.all()
        if days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            filtered = []
            for record in records:
                try:
                    when = datetime.fromisoformat(record.date)
                except ValueError:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                if when >= cutoff:
                    filtered.append(record)
            records = filtered
        words = sum(r.words for r in records)
        seconds = sum(r.duration for r in records)
        wpm = (words / (seconds / 60)) if seconds > 0 else 0.0
        return DictationStats(count=len(records), words=words, seconds=seconds, wpm=wpm)

    def _write(self, records: list[DictationRecord]) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps([asdict(r) for r in records], indent=1, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("could not write history: %s", exc)


class RecoveryStore:
    """Crash pad for dictations the decoder failed on.

    The vault-first guarantee only starts AFTER a successful transcription —
    before that point a decode error would throw the user's words away. So on
    failure the raw 16 kHz audio is written here as WAV (16-bit PCM, half the
    float32 size) and a tray "Recover" item lets the user retry once the model
    is healthy again.
    """

    MAX_FILES = 5
    MAX_AGE_DAYS = 7

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or paths.recovery_dir()

    @property
    def directory(self) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir

    def save(self, samples: np.ndarray) -> Path | None:
        """Persist failed-dictation audio. None on any error — recovery is
        best-effort and must never crash the error path itself."""
        if samples is None or len(samples) == 0:
            return None
        # ':' is illegal in Windows filenames, so the ISO timestamp is dashed.
        # Milliseconds keep two failures in the same second from colliding.
        stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-") + f"{int(time.time() * 1000) % 1000:03d}"
        path = self.directory / f"{stamp}.wav"
        try:
            path.write_bytes(_wav_bytes(samples))
        except OSError as exc:
            logger.error("could not save recovery audio: %s", exc)
            return None
        self._evict_beyond_cap()
        return path

    def pending(self) -> list[Path]:
        """Failed recordings, oldest first (names are timestamps → sortable)."""
        try:
            return sorted(self.directory.glob("*.wav"))
        except OSError:
            return []

    def load(self, path: Path) -> np.ndarray | None:
        """Read a recovery WAV back into 16 kHz float samples. Fixed 44-byte
        header — we only ever read files we wrote ourselves."""
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if len(data) <= 44:
            return None
        pcm = np.frombuffer(data[44:], dtype="<i2")
        return (pcm.astype(np.float32) / 32767.0).copy()

    def delete(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def prune_old(self) -> None:
        cutoff = time.time() - self.MAX_AGE_DAYS * 86_400
        for path in self.pending():
            try:
                if path.stat().st_mtime < cutoff:
                    self.delete(path)
            except OSError:
                continue

    def _evict_beyond_cap(self) -> None:
        files = self.pending()
        for path in files[: max(0, len(files) - self.MAX_FILES)]:
            self.delete(path)


def _wav_bytes(samples: np.ndarray) -> bytes:
    """16-bit PCM mono WAV with the canonical 44-byte header."""
    pcm = np.clip(np.asarray(samples, dtype=np.float32) * 32767.0, -32768, 32767)
    pcm = pcm.astype("<i2").tobytes()
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + len(pcm)),
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack("<H", 1),  # PCM
            struct.pack("<H", 1),  # mono
            struct.pack("<I", SAMPLE_RATE),
            struct.pack("<I", SAMPLE_RATE * 2),  # byte rate
            struct.pack("<H", 2),  # block align
            struct.pack("<H", 16),  # bits per sample
            b"data",
            struct.pack("<I", len(pcm)),
        ]
    )
    return header + pcm
