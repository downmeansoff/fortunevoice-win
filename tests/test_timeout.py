"""Ported from Tests/FortuneVoiceTests/TimeoutTests.swift."""

import time

import pytest

from fortunevoice.timeout import TimeoutExpired, run


def test_times_out_when_work_never_finishes():
    """Work that never finishes and ignores cancellation must not wedge the
    caller: the deadline has to fire and raise."""
    started = time.monotonic()
    with pytest.raises(TimeoutExpired):
        run(0.2, lambda: time.sleep(5))
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"timeout should fire promptly, took {elapsed:.2f}s"


def test_returns_immediately_when_work_finishes_fast():
    started = time.monotonic()

    def work() -> int:
        time.sleep(0.05)
        return 42

    assert run(5.0, work) == 42
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"should not wait for the deadline, took {elapsed:.2f}s"


def test_propagates_work_error():
    class Boom(Exception):
        pass

    def work():
        raise Boom()

    with pytest.raises(Boom):
        run(5.0, work)
