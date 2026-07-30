"""Ported from Tests/FortuneVoiceTests/SerialGateTests.swift.

The gate serialises decodes. Its waits MUST be bounded: an unbounded version
shipped once on macOS and turned a single wedged decode into a permanently
dead app — the holder never released, every later decode blocked forever, and
the state machine never returned to idle, which silently killed the hotkey for
the rest of the session.
"""

import threading
import time

from fortunevoice.transcriber import SerialGate


def test_acquires_when_free():
    gate = SerialGate()
    assert gate.acquire(1)


def test_wait_times_out_when_the_holder_never_releases():
    """The regression test. A holder that never releases must not trap the
    next caller — it gets refused and can fail fast."""
    gate = SerialGate()
    assert gate.acquire(1)  # held, never released

    started = time.monotonic()
    second = gate.acquire(0.2)
    waited = time.monotonic() - started

    assert not second, "a wedged holder must not block the next caller forever"
    assert waited < 2.0, "the wait must end near its own timeout"


def test_release_hands_off_to_a_waiter():
    gate = SerialGate()
    assert gate.acquire(1)

    got: list[bool] = []

    def waiter() -> None:
        got.append(gate.acquire(5))

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)  # let it park
    gate.release()
    thread.join(timeout=5)

    assert got == [True]


def test_expired_waiter_does_not_consume_a_later_release():
    """A timed-out waiter must be dropped from the queue, not left to soak up
    a later release and leave the gate held by nobody."""
    gate = SerialGate()
    assert gate.acquire(1)

    assert not gate.acquire(0.2)

    gate.release()  # the original holder finally releases

    assert gate.acquire(1), "the gate must be free again after the holder released"


def test_fifo_order():
    """Two waiters are served in arrival order — a streaming pass must not
    jump ahead of the final decode that queued before it."""
    gate = SerialGate()
    assert gate.acquire(1)
    order: list[int] = []

    def waiter(index: int) -> None:
        if gate.acquire(5):
            order.append(index)
            time.sleep(0.05)
            gate.release()

    first = threading.Thread(target=waiter, args=(1,))
    first.start()
    time.sleep(0.1)
    second = threading.Thread(target=waiter, args=(2,))
    second.start()
    time.sleep(0.1)

    gate.release()
    first.join(timeout=5)
    second.join(timeout=5)

    assert order == [1, 2]
