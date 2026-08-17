"""The dictation pipeline.

`decide_delivery` — which route a transcript takes — is pinned in
test_delivery. This is the rest: what actually happens to the user's words
once that decision is made, and the promise the whole app is built on, that
nothing is ever silently discarded.

Every external edge is replaced: no microphone, no model, no typing into other
windows, no sound. What runs is the app's own ordering and its guards.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

from fortunevoice import app as app_module  # noqa: E402
from fortunevoice.app import App, State  # noqa: E402
from fortunevoice.transcriber import Result  # noqa: E402


@pytest.fixture
def app(monkeypatch):
    """An App with every edge stubbed and every side effect recorded."""
    application = App()
    application._set_state(State.IDLE)

    typed: list[str] = []
    held: list[tuple[str, str]] = []
    sounds: list[str] = []

    monkeypatch.setattr(app_module.injector, "inject",
                        lambda text: typed.append(text) or True)
    monkeypatch.setattr(app_module.injector, "focused_element_is_editable",
                        lambda: True)
    monkeypatch.setattr(app_module.injector, "set_clipboard_text",
                        lambda text: pytest.fail("the clipboard must stay untouched"))
    monkeypatch.setattr(app_module.winapi, "foreground_window", lambda: 42)
    monkeypatch.setattr(app_module.winapi, "foreground_app_name", lambda: "Code.exe")
    monkeypatch.setattr(app_module.sound, "play", sounds.append)
    monkeypatch.setattr(application, "_hold",
                        lambda reason, text: held.append((reason, text)))

    application.typed = typed
    application.held = held
    application.sounds = sounds
    return application


def a_result(text="привет", logprob=-0.2):
    return Result(text=text, avg_logprob=logprob)


def deliver(app, text, **kwargs):
    samples = np.zeros(16_000, dtype=np.float32)
    fields = dict(raw=text, samples=samples, result=a_result(text),
                  key_up=app_module.time.monotonic(),
                  started=app_module.time.monotonic(),
                  stt_ms=400.0, cleanup_ms=0.0, target_window=42)
    fields.update(kwargs)
    app._deliver(text, **fields)


# ── the vault promise ────────────────────────────────────────────────────


def test_the_transcript_is_saved_before_it_is_typed(app, monkeypatch):
    """The ordering the whole app is built on: History first, then the focused
    window. A crash, a stuck paste or a switched window after this point can
    lose the delivery but never the words."""
    order: list[str] = []
    real_add = app.store.add
    monkeypatch.setattr(app.store, "add",
                        lambda record: order.append("saved") or real_add(record))
    monkeypatch.setattr(app_module.injector, "inject",
                        lambda text: order.append("typed") or True)

    deliver(app, "привет")
    assert order == ["saved", "typed"]


def test_the_words_survive_a_failed_delivery(app, monkeypatch):
    """Typing failing is exactly the case the vault exists for."""
    monkeypatch.setattr(app_module.injector, "inject", lambda text: False)
    deliver(app, "не напечаталось")
    assert [r.transcript for r in app.store.all()] == ["не напечаталось"]
    assert app.held, "and the user is shown it, rather than left guessing"


def test_the_spoken_words_are_kept_when_cleanup_changed_them(app):
    """So a mis-edit by the LLM is always recoverable."""
    deliver(app, "Я хотел сказать это.", raw="ну э я хотел сказать это")
    record = app.store.all()[0]
    assert record.transcript == "Я хотел сказать это."
    assert record.raw == "ну э я хотел сказать это"


def test_no_raw_copy_when_cleanup_changed_nothing(app):
    """Storing the same text twice would double the history file for nothing."""
    deliver(app, "уже чисто")
    assert app.store.all()[0].raw is None


def test_an_empty_transcript_is_not_stored(app):
    deliver(app, "")
    assert app.store.all() == []
    assert app.typed == []


# ── routing the text ─────────────────────────────────────────────────────


def test_a_confident_field_is_typed_into(app):
    deliver(app, "привет")
    assert app.typed == ["привет"]
    assert "success" in app.sounds


def test_lost_focus_goes_to_the_panel_instead(app, monkeypatch):
    """Typing into whatever the user switched to would put their words in a
    stranger's window."""
    monkeypatch.setattr(app_module.winapi, "foreground_window", lambda: 999)
    deliver(app, "привет")
    assert app.typed == []
    assert app.held


def test_a_stale_transcript_gets_no_success_tone(app):
    """The user has moved on. A chime for something that did NOT reach their
    cursor reads as "done" when it is not."""
    long_ago = app_module.time.monotonic() - app.STALE_PASTE_LIMIT - 1
    deliver(app, "привет", started=long_ago)
    assert app.typed == []
    assert "success" not in app.sounds


def test_a_failed_injection_is_held_rather_than_dropped(app, monkeypatch):
    monkeypatch.setattr(app_module.injector, "inject", lambda text: False)
    deliver(app, "привет")
    assert app.held and app.held[0][1] == "привет"


def test_nothing_reaches_the_clipboard_on_any_path(app, monkeypatch):
    """The macOS build stopped routing dictations through the clipboard, and
    the fixture fails the test if anything touches it. Every route here.
    """
    monkeypatch.setattr(app_module.injector, "inject", lambda text: False)
    deliver(app, "первое")
    monkeypatch.setattr(app_module.winapi, "foreground_window", lambda: 999)
    deliver(app, "второе")


def test_the_last_transcript_is_remembered_for_retyping(app):
    deliver(app, "запомни это")
    assert app.last_transcript == "запомни это"


def test_voice_commands_are_applied_before_anything_sees_the_text(app):
    """History, the panel and the typed output must agree on what was said."""
    from fortunevoice import config

    config.set("FVVoiceCommands", True)
    deliver(app, "Первая. Новая строка. Вторая.")
    assert app.typed == ["Первая.\nВторая."]
    assert app.store.all()[0].transcript == "Первая.\nВторая."


# ── what gets measured ───────────────────────────────────────────────────


def test_the_outcome_is_recorded(app):
    from fortunevoice import metrics

    deliver(app, "привет")
    assert metrics.read_all()[-1]["outcome"] == "pasted"


def test_blind_typing_is_recorded_as_such(app, monkeypatch):
    """Windows would not confirm an editable field. It is typed anyway — most
    terminals and Electron apps answer "unknown" — but the distinction has to
    survive into the numbers, or a wrong-window problem is invisible."""
    monkeypatch.setattr(app_module.injector, "focused_element_is_editable",
                        lambda: None)
    from fortunevoice import metrics

    deliver(app, "привет")
    assert metrics.read_all()[-1]["outcome"] == "pasted-blind"


def test_a_skipped_cleanup_is_recorded_as_skipped(app):
    """Under 20 ms means no HTTP round-trip happened at all."""
    from fortunevoice import metrics

    deliver(app, "привет", cleanup_ms=0.0)
    assert metrics.read_all()[-1]["cleanup_skipped"] is True


# ── finishing a recording ────────────────────────────────────────────────


class FakeRecorder:
    def __init__(self, samples):
        self.samples = samples
        self.stopped = 0
        self.is_recording = True

    def stop(self):
        self.stopped += 1
        self.is_recording = False
        return self.samples


def test_an_accidental_tap_is_dropped_without_a_decode(app, monkeypatch):
    """Below MIN_SAMPLES is a key brushed, not a word. Decoding it costs a
    full model run and whatever the model invents out of room noise."""
    started: list = []
    monkeypatch.setattr(app_module.threading, "Thread",
                        lambda **kwargs: pytest.fail("no pipeline for a tap"))
    app.recorder = FakeRecorder(np.zeros(100, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._finish_dictation(app_module.time.monotonic(), 42)
    assert app.state == State.IDLE
    assert started == []


def test_finishing_is_ignored_unless_recording(app):
    app.recorder = FakeRecorder(np.zeros(16_000, dtype=np.float32))
    app._set_state(State.IDLE)
    app._finish_dictation(app_module.time.monotonic(), 42)
    assert app.recorder.stopped == 0


def test_a_long_dictation_gets_a_longer_watchdog(app, monkeypatch):
    """A flat 90 s would time out a legitimate multi-minute recording and lose
    it. The budget scales with how much audio there is to chew."""
    captured: dict = {}

    class FakeThread:
        def __init__(self, target=None, args=(), **kwargs):
            captured["timeout"] = args[-1]

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app, "_arm_watchdog", lambda seconds: None)
    app.recorder = FakeRecorder(np.zeros(16_000 * 120, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._finish_dictation(app_module.time.monotonic(), 42)
    assert captured["timeout"] == pytest.approx(360.0), "120 s of audio × 3"


# ── an empty transcript ──────────────────────────────────────────────────


def empty_args(app, level):
    return dict(
        samples=np.full(16_000, level, dtype=np.float32),
        result=a_result(""), audio_level=level, audio_seconds=1.0, stt_ms=100.0,
        key_up=app_module.time.monotonic(), stream_passes=0, decode_timeout=5.0,
        target_window=42, started=app_module.time.monotonic(),
    )


def test_loud_audio_that_decoded_to_nothing_is_retried_once(app, monkeypatch):
    """That was real speech the decoder lost, not silence."""
    attempts: list = []

    def transcribe(samples):
        attempts.append(1)
        return a_result("расслышал со второго раза")

    monkeypatch.setattr(app.transcriber, "transcribe", transcribe)
    app._handle_empty(**empty_args(app, 0.5))
    assert len(attempts) == 1
    assert app.typed == ["расслышал со второго раза"]


def test_a_retried_delivery_is_marked_as_such(app, monkeypatch):
    from fortunevoice import metrics

    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: a_result("со второго раза"))
    app._handle_empty(**empty_args(app, 0.5))
    assert metrics.read_all()[-1]["retried"] is True


def test_audio_the_decoder_lost_twice_is_kept_for_a_manual_retry(app, monkeypatch):
    """Two failed decodes on loud audio. Throwing it away would be the app
    eating words it definitely heard."""
    monkeypatch.setattr(app.transcriber, "transcribe", lambda samples: a_result(""))
    app._handle_empty(**empty_args(app, 0.5))
    assert app.recovery.pending(), "the audio is recoverable from the tray"


def test_silence_is_not_kept_and_is_explained(app, monkeypatch):
    """Nothing was said. Saving room tone would fill the recovery folder with
    nothing, and a silent drop reads as "the app ate my words"."""
    said: list = []
    app.on_notify = lambda title, body: said.append(title)
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: pytest.fail("silence must not be re-decoded"))
    app._handle_empty(**empty_args(app, 0.001))
    assert app.recovery.pending() == []
    assert said, "the user is told the microphone heard nothing"
    assert "error" in app.sounds


# ── cancelling ───────────────────────────────────────────────────────────


def test_escape_drops_the_recording_without_decoding_it(app, monkeypatch):
    """A misfired hotkey otherwise costs a full decode and whatever the model
    invents out of room noise."""
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: pytest.fail("a cancelled dictation is not decoded"))
    app.recorder = FakeRecorder(np.zeros(16_000, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._on_cancel()
    assert app.state == State.IDLE
    assert app.store.all() == []
    assert app.recorder.stopped == 1


def test_cancelling_tells_the_user_it_worked(app):
    """`_on_cancel` used to raise on a `recovery.clear()` that does not exist,
    which skipped the sound and the pill below it. The recording did stop and
    the controller's error handler put the state back — so Esc "worked" while
    giving the user no sign of it at all."""
    app.recorder = FakeRecorder(np.zeros(16_000, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._on_cancel()
    assert "cancel" in app.sounds


def test_cancelling_keeps_earlier_recoverable_audio(app):
    """The recovery folder holds audio from EARLIER dictations the decoder
    lost. Cancelling this one must not throw those away."""
    app.recovery.save(np.full(16_000, 0.3, dtype=np.float32))
    kept = len(app.recovery.pending())
    assert kept == 1

    app.recorder = FakeRecorder(np.zeros(16_000, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._on_cancel()
    assert len(app.recovery.pending()) == kept


def test_cancelling_when_nothing_is_recording_does_nothing(app):
    app.recorder = FakeRecorder(np.zeros(16_000, dtype=np.float32))
    app._set_state(State.IDLE)
    app._on_cancel()
    assert app.recorder.stopped == 0


# ── retyping the last dictation ──────────────────────────────────────────


def test_retype_types_the_last_transcript(app, monkeypatch):
    monkeypatch.setattr(app, "RETYPE_DELAY", 0.0)
    monkeypatch.setattr(app_module.injector, "release_held_modifiers", lambda: None)
    app.last_transcript = "напечатай снова"
    assert app.retype_last() is True

    deadline = app_module.time.monotonic() + 3
    while not app.typed and app_module.time.monotonic() < deadline:
        app_module.time.sleep(0.01)
    assert app.typed == ["напечатай снова"]


def test_retype_does_nothing_without_a_last_dictation(app):
    app.last_transcript = ""
    assert app.retype_last() is False
    assert app.typed == []


# ── when the decoder itself fails ────────────────────────────────────────


def test_a_crashed_decode_keeps_the_audio_and_says_so(app, monkeypatch):
    """The vault-first guarantee only starts AFTER a successful decode, so
    this is the one path where the user's words exist nowhere else. Losing
    them here is the app eating a dictation outright."""
    said: list = []
    app.on_notify = lambda title, body: said.append(body)
    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: (_ for _ in ()).throw(RuntimeError("CUDA died")))

    samples = np.full(16_000, 0.3, dtype=np.float32)
    app._pipeline(samples, None, app_module.time.monotonic(), 42, 5.0)

    assert app.recovery.pending(), "the audio is recoverable from the tray"
    assert said and "Recover" in said[0], "and the user is told where to find it"
    assert "error" in app.sounds


def test_a_crashed_decode_is_recorded_as_an_error(app, monkeypatch):
    """Otherwise a run of failures is invisible in the numbers."""
    from fortunevoice import metrics

    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: (_ for _ in ()).throw(RuntimeError("boom")))
    app._pipeline(np.full(16_000, 0.3, dtype=np.float32),
                  None, app_module.time.monotonic(), 42, 5.0)
    assert metrics.read_all()[-1]["outcome"] == "error"


def test_a_crashed_decode_returns_the_app_to_idle(app, monkeypatch):
    """A state stuck outside idle silently kills the hotkey for the rest of
    the session."""
    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: (_ for _ in ()).throw(RuntimeError("boom")))
    app._set_state(State.PROCESSING)
    app._pipeline(np.full(16_000, 0.3, dtype=np.float32),
                  None, app_module.time.monotonic(), 42, 5.0)
    assert app.state == State.IDLE


# ── subtitle boilerplate hallucinated out of silence ─────────────────────


def pipeline_with(app, monkeypatch, text, level):
    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: a_result(text, logprob=-0.3))
    samples = np.full(16_000, level, dtype=np.float32)
    app._pipeline(samples, None, app_module.time.monotonic(), 42, 5.0)


def test_words_invented_out_of_silence_are_never_typed(app, monkeypatch):
    """Whisper answers silence with subtitle boilerplate — «Продолжение
    следует», "Thanks for watching". Typing that puts words in the user's
    document that nobody said. Reported live: it happened on a silent press."""
    pipeline_with(app, monkeypatch, "Продолжение следует.", level=0.0005)
    assert app.typed == []


def test_but_they_are_still_saved_so_a_false_positive_is_recoverable(app, monkeypatch):
    """Judged on the audio, not the model's own confidence — which reported
    0.000 on four out of four recordings of real silence here. A wrong call
    must not be a silent drop."""
    pipeline_with(app, monkeypatch, "Продолжение следует.", level=0.0005)
    assert [r.transcript for r in app.store.all()] == ["Продолжение следует."]
    assert "error" in app.sounds


def test_real_speech_that_happens_to_say_it_is_typed(app, monkeypatch):
    """The guard is about silence, not about the phrase. Someone dictating
    those words on loud audio must still get them."""
    pipeline_with(app, monkeypatch, "Продолжение следует.", level=0.4)
    assert app.typed == ["Продолжение следует."]
