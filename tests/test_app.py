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
    # The pipeline loads the model when it finds none — correct in the app,
    # and a real 5.6 s Whisper load inside a unit test. A placeholder is
    # enough: every test that decodes stubs `transcribe` anyway.
    application.transcriber._model = object()

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
    monkeypatch.setattr(app_module.threading, "Thread",
                        lambda **kwargs: pytest.fail("no pipeline for a tap"))
    app.recorder = FakeRecorder(np.zeros(100, dtype=np.float32))
    app._set_state(State.RECORDING)
    app._finish_dictation(app_module.time.monotonic(), 42)
    assert app.state == State.IDLE
    assert app.typed == [], "and nothing reached the focused window"


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
            # By position, not args[-1]: the pipeline's argument list grows,
            # and the last one is now the generation counter.
            captured["timeout"] = args[4]

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app, "_arm_watchdog", lambda seconds, samples=None: None)
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


# ── dropping the model when the app is left open ─────────────────────────


def test_unloading_is_off_unless_asked_for(app):
    """The app should be instant by default. Measured: reloading costs 5.6 s,
    and that lands inside the next dictation."""
    from fortunevoice import config

    assert config.DEFAULTS["FVUnloadModelAfter"] == 0
    assert app._idle_unload_seconds() == 0


def test_the_setting_is_in_minutes(app):
    from fortunevoice import config

    config.set("FVUnloadModelAfter", 30)
    assert app._idle_unload_seconds() == 1800


def test_a_negative_setting_reads_as_never(app):
    from fortunevoice import config

    config.set("FVUnloadModelAfter", -5)
    assert app._idle_unload_seconds() == 0


def test_a_dictation_reloads_a_model_that_was_dropped(app, monkeypatch):
    """Without this the next dictation after an unload would raise inside the
    pipeline and be filed as a failed transcription — the words gone."""
    loads: list[int] = []
    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app, "_load_model",
                        lambda: loads.append(1) or setattr(app.transcriber, "_model", object()))
    monkeypatch.setattr(app.transcriber, "transcribe", lambda samples: a_result("вернулась"))
    app.transcriber._model = None

    app._pipeline(np.full(16_000, 0.3, dtype=np.float32),
                  None, app_module.time.monotonic(), 42, 5.0)
    assert loads, "the pipeline must load it rather than fail"
    assert app.typed == ["вернулась"]


def test_a_loaded_model_is_not_reloaded(app, monkeypatch):
    monkeypatch.setattr(app.transcriber, "cancel_warmup", lambda: None)
    monkeypatch.setattr(app, "_load_model",
                        lambda: pytest.fail("already loaded, nothing to do"))
    monkeypatch.setattr(app.transcriber, "transcribe", lambda samples: a_result("готово"))
    app._pipeline(np.full(16_000, 0.3, dtype=np.float32),
                  None, app_module.time.monotonic(), 42, 5.0)
    assert app.typed == ["готово"]


# ── the state machine has one owner at a time ────────────────────────────


def test_a_model_reload_during_a_recording_does_not_cancel_it(app, monkeypatch):
    """`_start_dictation` reloads the model in the background when the idle
    unload dropped it. `_load_model` drove global state unconditionally, so
    that background load announced LOADING and then IDLE *while the user was
    speaking* — and `_finish_dictation` returns early unless the state is
    RECORDING. The whole dictation was dropped at key-up."""
    monkeypatch.setattr(app.transcriber, "load", lambda: None)
    app._set_state(State.RECORDING)

    app._load_model(announce=False)

    assert app.state == State.RECORDING, "the recording must survive a reload"


def test_a_finished_pipeline_does_not_stop_a_dictation_that_already_started(app):
    """The pipeline's `finally` forced IDLE with no check that it still owned
    the state. Let go, start talking again immediately, and the first
    pipeline's cleanup killed the second recording."""
    app._set_state(State.RECORDING)      # a new dictation, already under way
    app._finish_pipeline()
    assert app.state == State.RECORDING


def test_a_finished_pipeline_does_return_an_idle_app_to_idle(app):
    app._set_state(State.PROCESSING)
    app._finish_pipeline()
    assert app.state == State.IDLE


def test_toggle_mode_still_stops_on_silence(app, monkeypatch):
    """The auto-stop posts its stop through the same "release" event the
    hotkey uses — and `_on_release` returns early in toggle mode, so it was
    discarded. Silence never ended a toggled recording, and neither did the
    300 s cap: the microphone stayed open until the user noticed."""
    from fortunevoice import config

    config.set("FVActivationMode", "toggle")
    stopped: list[int] = []
    monkeypatch.setattr(app, "_stop_dictation", lambda: stopped.append(1))

    app._events.put(("autostop", app_module.time.monotonic()))
    app._events.put(("quit", app_module.time.monotonic()))
    app._controller_loop()

    assert stopped, "an automatic stop must work in both modes"


def test_a_dictation_that_could_not_be_saved_says_so(app, monkeypatch):
    """The vault is why every later failure is survivable. If it fails the text
    is still typed — it is the user's dictation — but they have to be told this
    one is not recoverable, rather than finding out later that History has been
    quietly empty for a week."""
    said: list[str] = []
    app.on_notify = lambda title, body: said.append(title)
    monkeypatch.setattr(app.store, "add", lambda record: False)

    deliver(app, "не сохранилось")
    assert app.typed == ["не сохранилось"], "the words still reach the user"
    assert said, "and the failure is not silent"


def test_recovery_reloads_a_model_that_the_idle_unload_dropped(app, monkeypatch):
    """The last-resort path for words already lost once. After an idle unload
    it called transcribe() on nothing and answered with a bare error beep."""
    loads: list[int] = []
    monkeypatch.setattr(app, "_load_model",
                        lambda announce=True: loads.append(1) or
                        setattr(app.transcriber, "_model", object()))
    monkeypatch.setattr(app.transcriber, "transcribe",
                        lambda samples: a_result("спасено"))
    app.recovery.save(np.full(16_000, 0.3, dtype=np.float32))
    app.transcriber._model = None
    app._set_state(State.IDLE)

    app.recover_failed()
    deadline = app_module.time.monotonic() + 5
    while not loads and app_module.time.monotonic() < deadline:
        app_module.time.sleep(0.02)
    assert loads, "it must load the model rather than beep"


def test_a_wedged_pipeline_keeps_the_audio_and_explains(app, monkeypatch):
    """The backstop for a step nobody bounded. It played a tone and forced
    IDLE — but the words exist nowhere yet at that point, so the dictation was
    simply gone and the user had a beep to go on."""
    said: list[str] = []
    app.on_notify = lambda title, body: said.append(title)
    app._set_state(State.PROCESSING)

    app._arm_watchdog(0.05, np.full(16_000, 0.3, dtype=np.float32))
    deadline = app_module.time.monotonic() + 3
    while app.state != State.IDLE and app_module.time.monotonic() < deadline:
        app_module.time.sleep(0.02)

    assert app.state == State.IDLE
    assert app.recovery.pending(), "the audio must survive for a retry"
    assert said, "and the user must be told where it went"


def test_a_late_pipeline_does_not_touch_a_newer_dictation(app):
    """State alone is not enough to decide ownership: a SECOND dictation can
    already be in PROCESSING when a wedged first one finally returns, and
    cancelling its watchdog would switch off the one guard that catches a
    stuck pipeline."""
    app._generation = 7
    app._set_state(State.PROCESSING)
    cancelled: list[int] = []
    app._cancel_watchdog = lambda: cancelled.append(1)

    app._finish_pipeline(generation=6)      # the older run, finishing late

    assert app.state == State.PROCESSING, "the newer dictation is left alone"
    assert cancelled == [], "and keeps its watchdog"

    app._finish_pipeline(generation=7)      # the current one
    assert app.state == State.IDLE


def test_a_late_release_still_stops_the_recording(app, monkeypatch):
    """A release is a TERMINATOR — acting on it late is strictly better than
    not at all. It used to be dropped with the press filter, which left the app
    recording with the key already up, the microphone open and the pill saying
    "Listening", until the 300 s cap typed five minutes of room noise into
    whatever had focus by then. The controller can easily be busy for over a
    second inside _start_dictation: opening a Bluetooth microphone, or the
    cleanup warmup's one-second probe of Ollama."""
    stopped: list[int] = []
    monkeypatch.setattr(app, "_stop_dictation", lambda: stopped.append(1))
    monkeypatch.setattr(app_module.config, "get_str",
                        lambda key: "hold" if key == "FVActivationMode"
                        else app_module.config.DEFAULTS.get(key, ""))

    stale = app_module.time.monotonic() - 5.0     # five seconds behind
    app._events.put(("release", stale))
    app._events.put(("quit", app_module.time.monotonic()))
    app._controller_loop()

    assert stopped == [1], "the recording must still be ended"


def test_a_late_press_is_still_dropped(app, monkeypatch):
    """The original reasoning holds for a press: acting on one the user has
    given up on would start a recording they never asked for."""
    started: list[int] = []
    monkeypatch.setattr(app, "_on_press", lambda: started.append(1))

    app._events.put(("press", app_module.time.monotonic() - 5.0))
    app._events.put(("quit", app_module.time.monotonic()))
    app._controller_loop()

    assert started == []


# ── a load that does not own the state must not take it ──────────────────


def test_the_key_down_reload_does_not_announce_over_the_recording(app, monkeypatch):
    """The call site, not the helper.

    `_start_dictation` starts the reload with the bare bound method, so while
    `_load_model` defaulted to announce=True that background load set LOADING
    and then IDLE *underneath a live recording*. `_stop_dictation` returns
    early unless the state is RECORDING, so the dictation was dropped at
    key-up with nothing typed, nothing saved and no sound — and the microphone
    left open, because the matching stop never ran either.
    """
    import threading

    entered = threading.Event()
    release = threading.Event()
    announced: list = []

    def slow_load():
        entered.set()
        release.wait(3)
        app.transcriber._model = object()

    real_set_state = app._set_state
    monkeypatch.setattr(app, "_set_state",
                        lambda state, *a, **k: announced.append(state)
                        or real_set_state(state, *a, **k))
    monkeypatch.setattr(app.recorder, "start", lambda device="": None)
    monkeypatch.setattr(app.transcriber, "load", slow_load)
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)
    monkeypatch.setattr(app.cleaner, "warmup", lambda: None)
    monkeypatch.setattr(app, "_arm_auto_stop", lambda: None)
    monkeypatch.setattr(app, "_start_streaming", lambda *a, **k: None, raising=False)
    app.transcriber._model = None
    app._set_state(State.IDLE)

    app._start_dictation()
    assert app.state is State.RECORDING
    assert entered.wait(3), "the reload thread never reached the load"
    assert State.LOADING not in announced, (
        "a reload running underneath a dictation must not announce its own "
        "state — the pill flashed 'Loading' over 'Listening'")

    release.set()
    for thread in threading.enumerate():
        if thread.name == "model-reload":
            thread.join(3)

    assert app.state is State.RECORDING, "the reload must run underneath the recording"


def test_a_successful_retry_leaves_the_error_state(app, monkeypatch):
    """The tray's "Retry model" loaded the model and then left the app in
    ERROR — where the hotkey is gated — so the only real fix was a restart."""
    monkeypatch.setattr(app.transcriber, "unload", lambda: True)
    monkeypatch.setattr(app.transcriber, "load",
                        lambda: setattr(app.transcriber, "_model", object()))
    app._set_state(State.ERROR, "Model failed to load")

    app._load_model()

    assert app.state is State.IDLE


def test_changing_the_model_actually_drops_the_old_one(app, monkeypatch):
    """`Transcriber.load()` is a no-op while a model is resident, so a reload
    that did not unload first did nothing at all: picking a different Whisper
    model in Settings kept using the old one until the next launch."""
    import threading

    order: list[str] = []
    monkeypatch.setattr(app.transcriber, "unload",
                        lambda: order.append("unload") or True)
    monkeypatch.setattr(app.transcriber, "load", lambda: order.append("load"))

    app.reload_model()
    for thread in threading.enumerate():
        if thread.name == "model-reload":
            thread.join(3)

    assert order == ["unload", "load"]


def test_a_dropped_stale_press_closes_the_pre_roll_microphone(app, monkeypatch):
    """The arm that came with the press had already opened the microphone.
    Dropping only the press left it open with the buffer growing, and
    `AudioRecorder.start` returns early when it is already recording — so the
    next dictation was handed that buffer, with everything said in the room in
    between."""
    stops: list[int] = []
    monkeypatch.setattr(app.recorder, "stop", lambda: stops.append(1))
    app._armed = app_module.time.monotonic()

    app._events.put(("press", app_module.time.monotonic() - 5.0))
    app._events.put(("quit", app_module.time.monotonic()))
    app._controller_loop()

    assert stops, "the microphone the arm opened has to be closed"
    assert not app._armed


def test_streaming_honours_a_per_app_profile(app, monkeypatch):
    """FVStreaming is listed in profiles.OVERRIDABLE, and `_start_dictation`
    read it straight from config — so the profile was accepted, reported as
    applied, and ignored. The fixture's foreground app is "Code.exe"."""
    from fortunevoice import config

    monkeypatch.setattr(app.recorder, "start", lambda device="": None)
    monkeypatch.setattr(app.transcriber, "warmup", lambda: None)
    monkeypatch.setattr(app.transcriber, "reset_session_language", lambda: None)
    monkeypatch.setattr(app.cleaner, "warmup", lambda: None)
    monkeypatch.setattr(app, "_arm_auto_stop", lambda: None)
    config.set("FVStreaming", True)
    config.set("FVAppProfiles", {"Code.exe": {"FVStreaming": False}})
    app._session = None
    app._set_state(State.IDLE)
    try:
        app._start_dictation()
        assert app._session is None, "the profile switched streaming off"
    finally:
        config.set("FVAppProfiles", {})
        app._session = None


# ── what counts as still talking ─────────────────────────────────────────


def test_a_normal_voice_keeps_the_toggle_recording_alive(app):
    """The threshold was a bare 0.2 RMS — four times the level at which the
    app warns the microphone is dead. A normal voice never reached it, so in
    toggle mode `_last_loud` stayed pinned at the start and the recording was
    cut off four seconds in, mid-sentence, with the pill still saying
    "Listening"."""
    app._set_state(State.RECORDING)
    app._peak_level = 0.0
    app._last_loud = 0.0

    app._on_level(0.06)          # ordinary speech at ordinary gain

    assert app._last_loud > 0.0, "speech has to count as speech"


def test_room_tone_does_not_keep_it_alive(app):
    """The other half: a threshold low enough to hear a quiet microphone must
    still let silence be silence, or toggle mode never stops on its own."""
    app._set_state(State.RECORDING)
    app._peak_level = 0.4        # the user has been talking loudly
    app._last_loud = 0.0

    app._on_level(0.004)         # the room with nobody speaking

    assert app._last_loud == 0.0


# ── a press that lands while the app is busy ─────────────────────────────


def test_a_press_during_processing_is_resumed_when_the_state_comes_back(app, monkeypatch):
    """From a real session log, four dictations in:

        21:24:41 hotkey DOWN (state = processing)
        21:24:41 key-up -> typed 1562 ms (313 chars)
        21:24:42 hotkey DOWN (state = idle)

    A sentence finished, the next one started straight away, and that press
    landed while the previous transcript was still being delivered. It was
    dropped in silence, and the user had to press again."""
    started: list[int] = []
    app._set_state(State.PROCESSING)
    app._start_dictation()                      # the press that lands too early
    assert app._pending_press, "the press has to be remembered"

    class Held:
        _held = True

    app._listener = Held()
    monkeypatch.setattr(app, "_start_dictation", lambda: started.append(1))

    app._finish_pipeline()

    assert app.state is State.IDLE
    assert started == [1], "and acted on once the state came back"


def test_a_press_the_user_gave_up_on_is_not_resumed(app, monkeypatch):
    """Pressed and released while waiting. Starting to record after they let
    go would be the app talking to itself."""
    started: list[int] = []
    app._set_state(State.PROCESSING)
    app._start_dictation()

    class Released:
        _held = False

    app._listener = Released()
    monkeypatch.setattr(app, "_start_dictation", lambda: started.append(1))

    app._finish_pipeline()

    assert started == []


def test_a_stale_press_is_not_resumed(app, monkeypatch):
    """Past a few seconds the user has moved on, and a recording they no
    longer expect is worse than the press having been dropped."""
    started: list[int] = []
    app._set_state(State.PROCESSING)
    app._start_dictation()
    app._pending_press = app_module.time.monotonic() - 30

    class Held:
        _held = True

    app._listener = Held()
    monkeypatch.setattr(app, "_start_dictation", lambda: started.append(1))

    app._finish_pipeline()

    assert started == []
