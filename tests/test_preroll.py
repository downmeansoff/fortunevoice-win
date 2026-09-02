"""Opening the microphone before the hold threshold has elapsed.

A modifier chord only counts as a press after HOLD_SECONDS, so a Ctrl+Alt
reached for by mistake starts nothing. Waiting that out before opening the
microphone would cost the user the first third of a second of what they said,
so the microphone opens at once and the wait becomes a pre-roll.

No real microphone: the recorder is replaced with one that records what it was
asked to do.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice.app import App, State  # noqa: E402
from fortunevoice.audio import AudioError  # noqa: E402


class FakeRecorder:
    def __init__(self, fail: bool = False) -> None:
        self.starts = 0
        self.stops = 0
        self.fail = fail
        self.is_recording = False

    def start(self, _device: str = "") -> None:
        if self.fail:
            raise AudioError("no microphone")
        self.starts += 1
        self.is_recording = True

    def stop(self):
        self.stops += 1
        self.is_recording = False
        return None


@pytest.fixture
def app():
    application = App()
    application.recorder = FakeRecorder()
    # A fresh App is LOADING until the model arrives; the user dictates from
    # IDLE, which is the state every one of these is about.
    application._set_state(State.IDLE)
    return application


def test_arming_opens_the_microphone(app):
    app._on_arm()
    assert app.recorder.starts == 1
    assert app._armed, "the moment it opened has to be remembered"


def test_arming_twice_opens_it_once(app):
    app._on_arm()
    app._on_arm()
    assert app.recorder.starts == 1


def test_a_tap_closes_it_again(app):
    """Otherwise a mistaken Ctrl+Alt leaves the microphone running until the
    next dictation happens to stop it."""
    app._on_arm()
    app._on_disarm()
    assert app.recorder.stops == 1
    assert not app._armed


def test_disarming_without_arming_does_nothing(app):
    app._on_disarm()
    assert app.recorder.stops == 0


def test_the_dictation_keeps_the_pre_rolled_audio(app, monkeypatch):
    """The point of the whole exercise: `_start_dictation` must not restart the
    recorder, because restarting throws away everything captured during the
    hold — which is exactly the speech this feature exists to keep."""
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)

    app._on_arm()
    armed_at = app._armed
    app._start_dictation()

    assert app.recorder.starts == 1, "the microphone was already open"
    assert app.recorder.stops == 0
    assert app.state == State.RECORDING
    assert app._recording_started == armed_at, "dated from when the mic opened"
    assert not app._armed, "consumed by the dictation"


def test_a_dictation_without_a_pre_roll_still_starts(app, monkeypatch):
    """An ordinary chord like ctrl+alt+space never arms — that path has to keep
    opening the microphone itself."""
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)

    app._start_dictation()
    assert app.recorder.starts == 1
    assert app.state == State.RECORDING


def test_a_disarm_during_a_real_recording_does_not_stop_it(app, monkeypatch):
    """In toggle mode the key-up arrives while a recording is legitimately
    running; disarming there would cut the user off mid-sentence."""
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)

    app._on_arm()
    app._start_dictation()
    app._armed = 1.0            # as if a stale arm were still recorded
    app._on_disarm()
    assert app.recorder.stops == 0
    assert app.state == State.RECORDING


def test_arming_is_silent_when_there_is_no_microphone(app):
    """The user has not committed to a dictation yet. If they go on to hold the
    chord, `_start_dictation` reports the failure properly."""
    app.recorder = FakeRecorder(fail=True)
    said: list[tuple[str, str]] = []
    app.on_notify = lambda title, body: said.append((title, body))

    app._on_arm()
    assert said == [], "no popup for a key the user may not have meant"
    assert not app._armed


def test_arming_is_ignored_unless_idle(app):
    app._set_state(State.PROCESSING)
    app._on_arm()
    assert app.recorder.starts == 0


def test_an_arm_that_never_becomes_a_dictation_closes_the_microphone(app, monkeypatch):
    """The microphone is opened at key-down and handed to `_start_dictation`
    300 ms later. If anything moved the app out of IDLE in between — another
    dictation still finishing, a model reload — that function returns early,
    and the arm it never consumed used to leave the microphone recording with
    nothing left to stop it.
    """
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)

    app._on_arm()
    assert app.recorder.is_recording

    # LOADING, not PROCESSING: a dictation now starts over a decode on
    # purpose (FVOverlapDictation), so PROCESSING no longer leaves an
    # arm stranded. LOADING still does — there is no model to decode
    # with, and the leak this test guards is unchanged.
    app._set_state(State.LOADING)   # busy by the time the hold completes
    app._start_dictation()

    assert not app._armed
    assert app.recorder.stops == 1, "the microphone must not be left open"
    assert not app.recorder.is_recording


def test_a_device_that_dies_during_the_pre_roll_closes_the_microphone(app):
    """The arm opened the microphone; the device it opened has just gone. The
    key-up that would normally close it is never coming, so nothing else
    would."""
    import time

    app._on_arm()
    assert app._armed, "precondition: the pre-roll opened the microphone"
    app._set_state(State.IDLE)

    app._on_interrupted()

    assert app.recorder.stops == 1, "the pre-roll microphone has to be closed"
    assert not app._armed
