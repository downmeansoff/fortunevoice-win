"""Bounded waits.

Port of Sources/FortuneVoice/Timeout.swift, with the same honest limitation:
this *abandons* work that overruns, it does not kill it. Neither Swift tasks
nor Python threads can be safely terminated from outside, and a decode wedged
inside CTranslate2 would ignore the attempt anyway.

Abandoning is enough because the thing being protected is the app's state
machine, not the CPU. The hotkey is gated on the app being idle, so a step
that never returns doesn't just lose one dictation; it kills dictation for
the rest of the session. A bounded wait hands control back; the orphaned
worker finishes into a result nobody reads, and the decoder gate (see
transcriber.py) keeps it from corrupting the next decode.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class TimeoutExpired(TimeoutError):
    pass


def run(seconds: float, work: Callable[[], T]) -> T:
    """Run `work` on a worker thread, raising TimeoutExpired after `seconds`.

    Exceptions raised by `work` propagate to the caller unchanged.
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def target() -> None:
        try:
            box["value"] = work()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=target, name="timeout-work", daemon=True)
    thread.start()
    if not done.wait(seconds):
        raise TimeoutExpired(f"work did not finish within {seconds:.1f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def run_or(seconds: float, work: Callable[[], T], fallback: T) -> T:
    """`run`, but any failure (timeout or exception) yields `fallback`."""
    try:
        return run(seconds, work)
    except BaseException:  # noqa: BLE001 - the fallback is the whole point
        return fallback
