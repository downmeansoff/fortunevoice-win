"""The dictation state machine.

Port of Sources/FortuneVoice/AppDelegate.swift. Every timeout, threshold and
ordering decision here was paid for with a real bug on macOS; the comments say
which one, because without that context they all look arbitrary and the next
person to "simplify" them will reintroduce the bug.

Threading, which is where this differs structurally from the Swift original:

* The hotkey hook calls us from inside a Windows hook callback that must
  return in milliseconds. It only enqueues; nothing else.
* A **controller** thread drains that queue and drives the state machine. It
  stays responsive — a press arriving mid-transcription is rejected on the
  spot by the state guard, exactly as the `@MainActor` version rejected it.
* Each dictation's transcribe → clean → deliver pipeline runs on its **own**
  worker thread, so a slow decode never wedges the controller.
"""

from __future__ import annotations

import enum
import queue
import threading
import time
from typing import Callable

import numpy as np

from . import config, dictionary, injector, metrics, paths, profiles, sound, winapi
from .audio import AudioError, AudioRecorder, max_window_rms, prewarm as audio_prewarm
from .cleaner import OllamaCleaner, keep_alive_seconds
from .hotkey import HotkeyListener, parse as parse_hotkey
from .log import get as get_logger
from .store import DictationRecord, DictationStore, RecoveryStore
from .strings import t
from .streaming import StreamingSession
from .textclean import (
    apply_voice_commands,
    collapse_repeats,
    is_hallucinated_silence,
    word_count,
)
from .timeout import run as run_with_timeout
from .transcriber import Result, Transcriber, TranscriberError

logger = get_logger("app")

SAMPLE_RATE = 16_000


def decide_delivery(*, stale: bool, focus_held: bool, editable: bool | None) -> str:
    """Where does this transcript go — into the app, or into the panel?

    Returns `"type"`, or the reason it cannot be typed: `"stale"`, `"focus"`,
    `"noedit"`. Kept pure and separate because it is the single most
    consequential decision in the app (it decides whether the user's words
    reach their cursor) and because the ordering carries meaning:

    * **stale first.** Twenty seconds after key-up the user has moved on;
      typing then lands in whatever they are doing NOW, which is worse than
      not typing at all.
    * **focus before editability.** If focus moved, the editability answer
      describes the wrong window.
    * **`editable is False`, not `not editable`.** `None` means "Windows would
      not say" — common in terminals and Electron apps, where the user really
      is typing into a real field. Treating unknown as "no" would refuse to
      type in exactly the apps people dictate into most, so unknown types
      anyway and the outcome is recorded as `pasted-blind`.
    """
    if stale:
        return "stale"
    if not focus_held:
        return "focus"
    if editable is False:
        return "noedit"
    return "type"


class State(enum.Enum):
    LOADING = "loading"
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


class App:
    # Drop the transcript instead of typing it when this much time passed since
    # key-up — the user has moved focus elsewhere by then.
    STALE_PASTE_LIMIT = 20.0
    # How long recording continues past key-up to catch the final syllable.
    # Users release the key on the last syllable and Whisper chews the final
    # word otherwise. This is a fixed cost on EVERY dictation, so it is kept to
    # the shortest span that still catches a trailing syllable.
    TAIL_CAPTURE = 0.2
    # Watchdog for a wedged transcription (should never trigger post-warmup).
    TRANSCRIBE_TIMEOUT = 90.0
    # Headroom over the decode timeout for cleanup and delivery.
    PROCESSING_WATCHDOG_SLACK = 30.0
    # What the LLM cleanup is allowed to SPEND. The predictor declines work
    # that doesn't fit rather than starting it and being cut off: macOS metrics
    # showed 24 of 60 cleanup runs burning a flat 3 s deadline and having their
    # output discarded — 3 s of waiting for the raw transcript the user would
    # have got anyway.
    CLEANUP_BUDGET = 1.5
    # Garbled (low-confidence) transcripts get more. Polishing punctuation on a
    # confident decode is worth ~1.5 s at most; reconstructing text the user
    # otherwise cannot read is worth the wait.
    SMART_FIX_BUDGET = 3.0
    # Backstop for an Ollama that stalls instead of generating, added on top of
    # the budget so a well-behaved call is never cut off by it.
    CLEANUP_DEADLINE_SLACK = 0.5
    # Below this the decode is treated as garbled and cleanup may rewrite words.
    LOW_CONFIDENCE_LOGPROB = -0.9
    # Dead-mic early warning: if the first seconds carry no signal at all, say
    # so while the user can still fix it, instead of after an empty transcript.
    NO_SIGNAL_CHECK_DELAY = 2.5
    NO_SIGNAL_LOUDNESS = 0.05
    # 4 s, not 2: a two-second pause is a normal mid-sentence think — cutting
    # there loses the continuation. 4 s only ends genuinely finished speech.
    SILENCE_STOP_SECONDS = 4.0
    # Hard cap. Hold mode relies on a key-up that can simply never arrive
    # (focus stolen mid-press, the machine sleeping), and without a cap that
    # leaves the app recording forever with the state stuck outside idle —
    # which silently kills the hotkey for the rest of the session.
    MAX_RECORDING_SECONDS = 300.0
    # Ignore only true accidental taps. Anything longer is transcribed; if it
    # turns out empty the empty-text guard handles it — we never drop a real
    # short word ("да", "нет", "стоп") unheard.
    MIN_SAMPLES = 1_600

    def __init__(self) -> None:
        self.recorder = AudioRecorder()
        self.transcriber = Transcriber()
        self.cleaner = OllamaCleaner()
        self.store = DictationStore()
        self.recovery = RecoveryStore()

        self._state = State.LOADING
        self._state_lock = threading.Lock()
        self._events: queue.Queue = queue.Queue()
        self._controller: threading.Thread | None = None
        self._listener: HotkeyListener | None = None
        self._session: StreamingSession | None = None

        self._watchdog: threading.Timer | None = None
        self._auto_stop: threading.Thread | None = None
        self._recording_started = 0.0
        self._last_loud = 0.0
        self._peak_level = 0.0
        self._no_signal_shown = False

        self.last_transcript = ""
        self.status_note = ""
        # Distinct UI failures already reported. See _on_ui_error.
        self._reported_ui_errors: set[str] = set()
        # When the microphone was opened for the pre-roll, 0.0 if it was not.
        self._armed = 0.0
        # Set on stop(), so the idle watcher ends with the app.
        self._stopping = threading.Event()
        # Last time a dictation finished, for FVUnloadModelAfter.
        self._last_dictation_at = time.monotonic()
        # UI hooks (the tray sets these).
        self.on_state_change: Callable[[State], None] | None = None
        self.on_notify: Callable[[str, str], None] | None = None
        # True once the Tk thread is up. Everything visual is optional: a
        # machine with a broken Tk still dictates, it just does it blind.
        self.ui_available = False

        self.recorder.on_level = self._on_level
        self.recorder.on_interrupted = lambda: self._events.put(("interrupted", time.monotonic()))

    # ── state ────────────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        with self._state_lock:
            return self._state

    def _set_state(self, state: State, note: str = "") -> None:
        with self._state_lock:
            if self._state == state and self.status_note == note:
                return
            self._state = state
            self.status_note = note
        self._update_overlay(state)
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception:  # noqa: BLE001 - a broken tray must not stop dictation
                logger.exception("state-change handler failed")

    def _on_ui_error(self, detail: str) -> None:
        """A UI callback raised. Told once per distinct failure, so a repaint
        that throws on every frame cannot turn into a stream of balloons."""
        if detail in self._reported_ui_errors:
            return
        self._reported_ui_errors.add(detail)
        self._notify(t("notify.ui_failed"), detail)

    def _notify(self, title: str, body: str) -> None:
        logger.info("%s — %s", title, body)
        if self.on_notify:
            try:
                self.on_notify(title, body)
            except Exception:  # noqa: BLE001
                logger.debug("notification failed", exc_info=True)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self.recovery.prune_old()
        self.store.prune(config.get_int("FVRetentionDays"))
        self._start_ui()

        self._controller = threading.Thread(
            target=self._controller_loop, name="controller", daemon=True
        )
        self._controller.start()

        self.rebind_hotkey()

        # A window that fails to build must not fail in silence.
        from .ui import ui as ui_thread

        ui_thread.on_error = self._on_ui_error

        threading.Thread(target=self._load_model, name="model-load", daemon=True).start()
        # Pay PortAudio's first-open cost now rather than out of the first
        # dictation's opening word. See audio.prewarm().
        threading.Thread(target=audio_prewarm, name="audio-warm", daemon=True).start()
        threading.Thread(target=self._watch_for_idle, name="idle-watch",
                         daemon=True).start()
        if config.get_bool("FVCleanupEnabled") or config.get_bool("FVSmartFix"):
            # Only pre-load the cleanup model when it will still be there by
            # the time it is wanted. With a short hold, warming at startup
            # means 2.2 GB of video memory occupied for five minutes after
            # every launch, released again without a single dictation having
            # happened. The hotkey-down warmup covers that case anyway.
            if keep_alive_seconds() >= 600:
                self.cleaner.warmup()
            threading.Thread(target=self._check_cleanup_reachable,
                             name="ollama-check", daemon=True).start()

    def _check_cleanup_reachable(self) -> None:
        """Say so, once, if cleanup is switched on but cannot happen.

        Without this the failure is invisible: dictation keeps working and
        types raw Whisper text, which looks exactly like the cleanup setting
        being off. Said once at startup and never again — a notification per
        dictation would be worse than the silence it replaces.
        """
        from . import ollama

        if ollama.ensure_running():
            return
        detail = (t("notify.ollama_missing") if ollama.executable() is None
                  else t("notify.ollama_down"))
        self._notify(t("notify.no_cleanup"), detail)

    def rebind_hotkey(self) -> bool:
        """(Re)install the keyboard hook for whatever FVHotkey now says.

        Called at startup and whenever Settings changes the shortcut. Doing it
        live matters: the alternative is telling the user to restart the app,
        and a shortcut they cannot try immediately is one they cannot tell is
        wrong. Returns False when the configured value did not parse and the
        fallback was used instead.
        """
        ok = True
        try:
            spec = parse_hotkey(config.get_str("FVHotkey"))
        except ValueError as exc:
            logger.error("%s — falling back to ctrl+alt+space", exc)
            self._notify(t("notify.bad_hotkey"), str(exc))
            spec = parse_hotkey("ctrl+alt+space")
            ok = False

        # Stop the old hook first. Two hooks for one chord would queue two
        # press events and start the dictation twice.
        if self._listener is not None:
            self._listener.stop()
        self._listener = HotkeyListener(
            spec,
            on_press=lambda: self._events.put(("press", time.monotonic())),
            on_release=lambda: self._events.put(("release", time.monotonic())),
            on_arm=lambda: self._events.put(("arm", time.monotonic())),
            on_disarm=lambda: self._events.put(("disarm", time.monotonic())),
        )
        self._listener.start()
        return ok

    def capture_chord(self, on_chord, on_cancel=None):
        """Record the next chord the user presses, anywhere on the system.

        The global hook is paused first: the chord a user reaches for when
        changing a shortcut is usually the one already set, and leaving the
        hook live would swallow it and start a dictation instead.
        """
        from .hotkey import ChordCapture

        self.pause_hotkey()
        capture = ChordCapture(on_chord, on_cancel)
        capture.start()
        return capture

    def pause_hotkey(self) -> None:
        """Stop listening for the global hotkey.

        Used while the user is recording a NEW shortcut. Without this the hook
        swallows the very chord they are trying to press — the current hotkey
        is exactly the one a user reaches for first — and starts a dictation
        instead of recording the key.
        """
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            logger.info("hotkey paused while a new shortcut is recorded")

    def resume_hotkey(self) -> None:
        """Reinstall the hook, picking up any change made meanwhile."""
        if self._listener is None:
            self.rebind_hotkey()

    def cancel_dictation(self) -> bool:
        """Throw away a recording in progress.

        Wired to Esc while recording. Without it the only way out of a
        misfired hotkey is to stay silent and wait for a decode of nothing,
        which still costs a second and still types whatever it hallucinated.
        """
        if self.state != State.RECORDING:
            return False
        self._events.put(("cancel", time.monotonic()))
        return True

    def stop(self) -> None:
        self._stopping.set()
        if self._listener:
            self._listener.stop()
        if self.ui_available:
            from .ui import ui

            ui.stop()
        self._events.put(("quit", time.monotonic()))

    # ── UI ───────────────────────────────────────────────────────────────

    def _start_ui(self) -> None:
        try:
            from .ui import ui
            from .ui.onboarding import needed, onboarding
        except Exception as exc:  # noqa: BLE001 - no Tk is survivable
            logger.warning("UI unavailable (%s) — running headless", exc)
            return
        self.ui_available = ui.start()
        if not self.ui_available:
            return
        if needed():
            onboarding.show(self)

    def open_main_window(self) -> None:
        if not self.ui_available:
            self.open_data_folder()  # the JSON files are the fallback UI
            return
        from .ui.main_window import window

        window.show(self)

    def show_onboarding(self) -> None:
        if not self.ui_available:
            return
        from .ui.onboarding import onboarding

        onboarding.show(self)

    def _pill(self):
        if not self.ui_available or not config.get_bool("FVOverlay"):
            return None
        from .ui.pill import pill

        return pill

    def _update_overlay(self, state: State) -> None:
        pill = self._pill()
        if pill is None:
            return
        if state is State.RECORDING:
            pill.show("no-signal" if self.status_note else "recording")
        elif state is State.PROCESSING:
            pill.show("processing")
        else:
            pill.hide()

    @property
    def hotkey_label(self) -> str:
        return self._listener.spec.label if self._listener else config.get_str("FVHotkey")

    def _load_model(self) -> None:
        self._set_state(State.LOADING)
        try:
            self.transcriber.load()
        except TranscriberError as exc:
            logger.error("model load failed: %s", exc)
            self._set_state(State.ERROR, "Model failed to load")
            self._notify("FortuneVoice couldn't load the model", str(exc).split("\n")[0])
            return
        self._set_state(State.IDLE)

    def reload_model(self) -> None:
        threading.Thread(target=self._load_model, name="model-reload", daemon=True).start()

    # ── controller ───────────────────────────────────────────────────────

    def _controller_loop(self) -> None:
        while True:
            kind, stamp = self._events.get()
            if kind == "quit":
                return
            # A press that waited behind something slow is not a press the user
            # still means; acting on it would start a recording they abandoned.
            if kind in ("press", "release") and time.monotonic() - stamp > 1.0:
                logger.debug("dropping stale %s event", kind)
                continue
            try:
                if kind == "press":
                    self._on_press()
                elif kind == "release":
                    self._on_release()
                elif kind == "arm":
                    self._on_arm()
                elif kind == "disarm":
                    self._on_disarm()
                elif kind == "interrupted":
                    self._on_interrupted()
                elif kind == "cancel":
                    self._on_cancel()
            except Exception:  # noqa: BLE001 - one bad event must not end the loop
                logger.exception("controller failed on %s", kind)
                self._set_state(State.IDLE)

    def _on_press(self) -> None:
        if config.get_str("FVActivationMode") == "toggle":
            if self.state == State.RECORDING:
                self._stop_dictation()
            else:
                self._start_dictation()
        else:
            self._start_dictation()

    def _on_release(self) -> None:
        if config.get_str("FVActivationMode") == "toggle":
            return
        self._stop_dictation()

    # ── the pre-roll ─────────────────────────────────────────────────────
    #
    # A modifier chord only counts as a press after HOLD_SECONDS, so that a
    # Ctrl+Alt reached for by mistake starts nothing. Waiting that long before
    # opening the microphone would cost the user the first third of a second
    # of speech — so the microphone opens at once, and the wait becomes a
    # pre-roll rather than a hole. If the hold never completes, the audio is
    # dropped and nothing else ever knew about it.

    def _on_arm(self) -> None:
        if self.state != State.IDLE or self._armed:
            return
        try:
            self.recorder.start(config.get_str("FVMicrophone"))
        except AudioError as exc:
            # Stay quiet here. The user has not committed to a dictation yet,
            # and if they do, _start_dictation reports it properly.
            logger.debug("could not open the microphone early: %s", exc)
            return
        self._armed = time.monotonic()

    def _on_disarm(self) -> None:
        if not self._armed:
            return
        self._armed = 0.0
        # Anything except an actual dictation in progress. Guarding on IDLE
        # instead was not enough: the leak this closes happens precisely when
        # the app is busy, so the one state that must be spared is RECORDING —
        # in toggle mode the key-up arrives while a recording is legitimately
        # running, and stopping it there cuts the user off mid-sentence.
        if self.state != State.RECORDING:
            self.recorder.stop()

    def _on_interrupted(self) -> None:
        """Input device died mid-recording. Don't throw away what was already
        captured — transcribe and deliver it (truncated is far better than
        erased), exactly like a normal key-up."""
        if self.state != State.RECORDING:
            return
        logger.warning("recording interrupted — salvaging captured audio")
        self._finish_dictation(time.monotonic(), winapi.foreground_window())

    def _on_cancel(self) -> None:
        """Esc during a recording: bin it.

        The opposite of `_on_interrupted`, which salvages. Here the user is
        saying they don't want this dictation, so the audio is dropped without
        a decode — no transcript, no history entry, nothing typed. A misfired
        hotkey otherwise costs a full decode and whatever the model invented
        out of room noise.
        """
        if self.state != State.RECORDING:
            return
        logger.info("dictation cancelled by the user")
        if self._session is not None:
            self._session.abort()
            self._session = None
        self.recorder.stop()
        # Nothing to clear from recovery: audio is written there only after a
        # decode fails, and a cancel happens before any decode. This called a
        # `recovery.clear()` that does not exist, so every Esc raised —
        # skipping the sound and the pill below and leaving the user with no
        # sign at all that the cancel had worked. Clearing would also have been
        # wrong: the folder holds audio from EARLIER failed dictations that the
        # user can still recover from the tray.
        sound.play("cancel")
        pill = self._pill()
        if pill is not None:
            pill.flash("cancelled")
        self._set_state(State.IDLE)

    # ── dictation ────────────────────────────────────────────────────────

    def _start_dictation(self) -> None:
        state = self.state
        logger.info("hotkey DOWN (state = %s)", state.value)
        if state != State.IDLE:
            # An arm that never became a dictation has to be undone here, or
            # the microphone opened at key-down stays open with nothing left
            # to close it. Reachable whenever something moved the app out of
            # IDLE during the 300 ms hold — a previous dictation still being
            # delivered, a model reload.
            self._on_disarm()
            return
        # Already open from the pre-roll, and holding the audio from before
        # the hold threshold elapsed — keep it, and date the recording from
        # when the microphone actually opened.
        armed_at, self._armed = self._armed, 0.0
        if not armed_at:
            try:
                self.recorder.start(config.get_str("FVMicrophone"))
            except AudioError as exc:
                logger.error("recording failed: %s", exc)
                self._notify(t("notify.no_audio"), str(exc))
                sound.play("error")
                return

        # Dropped by the idle watcher since the last dictation: start loading
        # it back now, so the 5.6 s overlaps with the user speaking instead of
        # landing entirely in the wait after they let go.
        if not self.transcriber.is_loaded:
            threading.Thread(target=self._load_model, name="model-reload",
                             daemon=True).start()

        self._recording_started = armed_at or time.monotonic()
        self._last_loud = self._recording_started
        self._peak_level = 0.0
        self._no_signal_shown = False
        self._set_state(State.RECORDING)
        sound.play("start")
        self._arm_auto_stop()

        # Load the cleanup model now, while the user is still talking — a cold
        # Ollama load costs ~9 s if it happens after they release.
        if config.get_bool("FVCleanupEnabled") or config.get_bool("FVSmartFix"):
            self.cleaner.warmup()
        # Same idea for Whisper: after a long idle the pipeline has to spin back
        # up, and that cost otherwise lands inside the key-up → paste wait.
        self.transcriber.reset_session_language()
        self.transcriber.warmup()

        if config.get_bool("FVStreaming"):
            # Hand the cleaner in so confirmed prefixes get cleaned DURING the
            # recording; at key-up only the tail is left to clean.
            live_cleaner = self.cleaner if config.get_bool("FVCleanupEnabled") else None
            self._session = StreamingSession(
                self.transcriber, self.recorder,
                cleaner=live_cleaner, vocabulary=dictionary.prompt_string(),
            )
            self._session.start()

    def _stop_dictation(self) -> None:
        if self.state != State.RECORDING:
            return
        key_up = time.monotonic()
        # Remember where the user was aiming so we never type into a window
        # they switched to while we were transcribing.
        target = winapi.foreground_window()
        # Tail capture; see TAIL_CAPTURE.
        time.sleep(self.TAIL_CAPTURE)
        self._finish_dictation(key_up, target)

    def _idle_unload_seconds(self) -> float:
        """After how long with no dictation Whisper is dropped. 0 = never."""
        return max(0, config.get_int("FVUnloadModelAfter")) * 60

    def _watch_for_idle(self) -> None:
        """Give the video memory back when the app has plainly been left open.

        Off by default. Measured here: idle with the model resident is 3180 MiB
        against 1088 with the app closed — ~2.1 GB of a 6 GB card held while
        nothing is happening. Reloading costs 5.6 s, and that lands inside a
        dictation, so this is only worth it on a small card left running all
        day. The user decides; nothing is quietly traded on their behalf.
        """
        while not self._stopping.is_set():
            seconds = self._idle_unload_seconds()
            if not seconds:
                self._stopping.wait(60)  # setting may be turned on later
                continue
            self._stopping.wait(min(60, seconds / 4))
            if self.state != State.IDLE or not self.transcriber.is_loaded:
                continue
            if time.monotonic() - self._last_dictation_at >= seconds:
                # The state stays IDLE, which is the truth: the app is ready
                # and waiting, it just needs a moment on the next press.
                self.transcriber.unload()

    def _arm_auto_stop(self) -> None:
        """Auto-stop the recording. In toggle mode a run of silence ends it, so
        the user never has to remember to. In BOTH modes the hard cap applies."""
        silence_stops = config.get_str("FVActivationMode") == "toggle"

        def watch() -> None:
            while self.state == State.RECORDING:
                time.sleep(0.4)
                now = time.monotonic()
                if silence_stops and now - self._last_loud > self.SILENCE_STOP_SECONDS:
                    self._events.put(("release", time.monotonic()))
                    return
                if winapi.key_is_down(winapi.VK_ESCAPE):
                    # Polled here rather than through a second keyboard hook:
                    # this thread already exists for the life of the recording,
                    # and another global hook is another thing that can wedge
                    # the input queue.
                    self._events.put(("cancel", time.monotonic()))
                    return
                if now - self._recording_started > self.MAX_RECORDING_SECONDS:
                    logger.warning("recording hit the %.0fs cap", self.MAX_RECORDING_SECONDS)
                    self._events.put(("release", time.monotonic()))
                    return

        self._auto_stop = threading.Thread(target=watch, name="auto-stop", daemon=True)
        self._auto_stop.start()

    def _on_level(self, level: float) -> None:
        """Called from the audio thread — keep it to arithmetic and one store."""
        pill = self._pill()
        if pill is not None:
            pill.push_level(level)
        if level > 0.2:
            self._last_loud = time.monotonic()
        self._peak_level = max(self._peak_level, level)
        if (
            not self._no_signal_shown
            and self.state == State.RECORDING
            and time.monotonic() - self._recording_started > self.NO_SIGNAL_CHECK_DELAY
            and self._peak_level < self.NO_SIGNAL_LOUDNESS
        ):
            self._no_signal_shown = True
            self._set_state(State.RECORDING, "no signal from the microphone")

    def _arm_watchdog(self, seconds: float) -> None:
        """Last-resort guard on PROCESSING.

        The hotkey is gated on the app being idle, so any pipeline step that
        never returns doesn't just lose one dictation — it kills dictation for
        the rest of the session, with no recovery and nothing on screen to
        explain it. The per-step timeouts are the real defense; this is the
        backstop for the step nobody thought to bound.
        """
        self._cancel_watchdog()

        def fire() -> None:
            if self.state != State.PROCESSING:
                return
            logger.error("processing never finished after %.0f s — forcing idle", seconds)
            sound.play("error")
            self._set_state(State.IDLE)

        self._watchdog = threading.Timer(seconds, fire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None

    def _finish_dictation(self, key_up: float, target_window: int) -> None:
        if self.state != State.RECORDING:
            return
        samples = self.recorder.stop()
        session, self._session = self._session, None
        audio_seconds = samples.size / SAMPLE_RATE
        logger.info("captured %.1fs of audio", audio_seconds)

        if samples.size <= self.MIN_SAMPLES:
            if session:
                session.abort()
            self._set_state(State.IDLE)
            return

        self._set_state(State.PROCESSING)
        # The watchdog must exceed the longest possible decode: a multi-minute
        # dictation can take longer than a flat 90 s. Scale it with audio length
        # so a legitimate long recording is never timed out and lost.
        decode_timeout = max(self.TRANSCRIBE_TIMEOUT, audio_seconds * 3)
        self._arm_watchdog(decode_timeout + self.PROCESSING_WATCHDOG_SLACK)

        threading.Thread(
            target=self._pipeline,
            args=(samples, session, key_up, target_window, decode_timeout),
            name="pipeline",
            daemon=True,
        ).start()

    def _pipeline(
        self,
        samples: np.ndarray,
        session: StreamingSession | None,
        key_up: float,
        target_window: int,
        decode_timeout: float,
    ) -> None:
        started = time.monotonic()
        audio_seconds = samples.size / SAMPLE_RATE
        stream_passes = session.pass_count if session else 0
        try:
            # Clear any warmup decode out of the pipeline so it can't sit in
            # front of the real one.
            self.transcriber.cancel_warmup()

            # The reload kicked off at key-down may still be running, or may
            # never have happened. Either way the decode cannot start without
            # it, and failing here would throw away a dictation the user has
            # already spoken.
            if not self.transcriber.is_loaded:
                self._load_model()

            stt_started = time.monotonic()
            # The prefix cleaned during recording is only usable when the
            # stitched decode is what we ended up with. Bind it here rather than
            # reading it later: a finish() that lost to the watchdog can still
            # set it from the background, and splicing that prefix onto the
            # batch text would duplicate the opening of the dictation.
            pre_cleaned: tuple[str, str] | None = None
            if session:
                try:
                    result = run_with_timeout(decode_timeout, lambda: session.finish(samples))
                    pre_cleaned = session.last_pre_cleaned
                except Exception as exc:  # noqa: BLE001
                    # Streaming failed — fall back to the plain batch path so
                    # the dictation is never lost.
                    logger.warning("streaming failed (%s), batch fallback", exc)
                    result = run_with_timeout(
                        decode_timeout, lambda: self.transcriber.transcribe(samples)
                    )
            else:
                result = run_with_timeout(
                    decode_timeout, lambda: self.transcriber.transcribe(samples)
                )
            stt_ms = (time.monotonic() - stt_started) * 1000
            stream_passes = session.pass_count if session else 0

            raw_text = result.text
            audio_level = max_window_rms(samples)

            if not raw_text:
                self._handle_empty(
                    samples, result, audio_level, audio_seconds, stt_ms, key_up,
                    stream_passes, decode_timeout, target_window, started,
                )
                return

            # Silence-hallucination guard: Whisper fills an empty room with
            # subtitle boilerplate ("Продолжение следует", "Thanks for
            # watching"). Judged on the audio, not on the model's own
            # no_speech_prob — that reported 0.000 on four out of four
            # recordings of real silence here. Still saved to History
            # (untyped), so a false positive is recoverable; never a truly
            # silent drop.
            if is_hallucinated_silence(raw_text, audio_level):
                logger.warning(
                    "silence hallucination (rms %.5f, no_speech %.2f) — saved, not typed",
                    audio_level, result.no_speech_prob,
                )
                self.store.add(
                    DictationRecord(
                        date=metrics.now(), words=word_count(raw_text),
                        duration=audio_seconds, app=None, transcript=raw_text,
                    )
                )
                sound.play("error")
                self._record_drop("silence", raw_text, audio_seconds, stt_ms, key_up,
                                  stream_passes, result)
                return

            text, cleanup_ms, pre_cleaned_words = self._run_cleanup(
                raw_text, result, pre_cleaned
            )
            self._deliver(
                text, raw_text, samples, result, key_up, started, stt_ms, cleanup_ms,
                target_window, retried=False, stream_passes=stream_passes,
                shadow=session.last_shadow if session else None,
                pre_cleaned_words=pre_cleaned_words,
            )
        except Exception as exc:  # noqa: BLE001 - the words are not lost, see below
            logger.error("transcription failed: %s", exc, exc_info=True)
            # Audio goes to the recovery pad; the tray gains a Recover item.
            self.recovery.save(samples)
            sound.play("error")
            self._notify(
                "FortuneVoice couldn't transcribe that",
                "The audio was saved — use “Recover failed dictation” in the tray menu.",
            )
            metrics.record(
                metrics.DictationMetric(
                    date=metrics.now(), capture_sec=audio_seconds,
                    stt_ms=(time.monotonic() - started) * 1000, cleanup_ms=0,
                    total_ms=(time.monotonic() - key_up) * 1000, chars=0,
                    outcome="error", cleanup_skipped=True, retried=False,
                    stream_passes=stream_passes, logprob=0.0,
                    model=self.transcriber.loaded_model or "?",
                    device=self.transcriber.device,
                )
            )
        finally:
            self._cancel_watchdog()
            self._set_state(State.IDLE)

    def _handle_empty(
        self, samples, result, audio_level, audio_seconds, stt_ms, key_up,
        stream_passes, decode_timeout, target_window, started,
    ) -> None:
        """Empty transcript. If the audio was actually loud, the decoder failed
        on real speech — retry once (timed) before giving up."""
        if audio_level > 0.02:
            logger.warning("empty transcript on loud audio (rms %.3f), retrying", audio_level)
            try:
                retry = run_with_timeout(
                    decode_timeout, lambda: self.transcriber.transcribe(samples)
                )
            except Exception:  # noqa: BLE001
                retry = None
            if retry and retry.text:
                self._deliver(
                    retry.text, retry.text, samples, retry, key_up, started, stt_ms, 0.0,
                    target_window, retried=True, stream_passes=stream_passes,
                )
                return

        logger.warning("empty transcript (rms %.3f), nothing to type", audio_level)
        if audio_level > 0.02:
            # Loud audio, two failed decodes — that was real speech the decoder
            # lost. Keep the audio for a manual retry.
            self.recovery.save(samples)
        # Never end a dictation without telling the user what happened — a
        # silent drop reads as "the app ate my words".
        sound.play("error")
        if audio_level < 0.006:
            self._notify(
                "FortuneVoice didn't hear anything",
                "The microphone picked up no signal — check the input device in the tray menu.",
            )
        self._record_drop(
            "silence" if audio_level < 0.006 else "empty", "", audio_seconds, stt_ms,
            key_up, stream_passes, result,
        )

    def _run_cleanup(
        self, raw_text: str, result: Result, pre_cleaned: tuple[str, str] | None
    ) -> tuple[str, float, int | None]:
        """Cleanup with its own safety net. Hard deadline: macOS metrics showed
        a cold Ollama load stalling this for ~16 s — raw Whisper text NOW beats
        perfect text later, so past the deadline we type raw and let the LLM
        finish unused."""
        # Whatever is in front decides. Cleanup punctuating a chat message is
        # right; cleanup punctuating `git rebase -i HEAD~3` into a sentence is
        # not, and a terminal is where that lands.
        target = winapi.foreground_app_name()
        cleanup_enabled = profiles.get_bool("FVCleanupEnabled", target)
        smart_fix = profiles.get_bool("FVSmartFix", target)
        low_confidence = result.avg_logprob < self.LOW_CONFIDENCE_LOGPROB
        if not (cleanup_enabled or (low_confidence and smart_fix)):
            return raw_text, 0.0, None

        started = time.monotonic()
        vocabulary = dictionary.prompt_string()
        # The confirmed prefix was already cleaned while the user was still
        # speaking, so only the unconfirmed tail is on the clock here. Low
        # confidence opts out: reconstructing garbled words needs the whole
        # text as context.
        if low_confidence:
            pre_cleaned = None
        to_clean = pre_cleaned[1] if pre_cleaned else raw_text
        budget = self.SMART_FIX_BUDGET if low_confidence else self.CLEANUP_BUDGET

        try:
            cleaned_part = run_with_timeout(
                budget + self.CLEANUP_DEADLINE_SLACK,
                lambda: self.cleaner.clean(
                    to_clean, low_confidence=low_confidence,
                    vocabulary=vocabulary, budget=budget,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - raw text is always acceptable
            logger.info("cleanup did not land in time (%s), using raw text", exc)
            cleaned_part = to_clean

        if pre_cleaned:
            text = self._assemble(pre_cleaned[0], cleaned_part, raw_text)
            pre_cleaned_words = word_count(pre_cleaned[0])
        else:
            text = cleaned_part
            pre_cleaned_words = None
        return text, (time.monotonic() - started) * 1000, pre_cleaned_words

    @staticmethod
    def _assemble(prefix: str, remainder: str, raw: str) -> str:
        """Join the prefix cleaned during recording with the freshly cleaned
        tail. Same safety net as the whole-text path: if the assembled result
        lost more than ~35% of the spoken words, something ate content — use
        the raw transcript. Losing punctuation beats losing sentences."""
        joined = collapse_repeats(" ".join(p for p in (prefix, remainder) if p))
        raw_words = word_count(raw)
        joined_words = word_count(joined)
        if not joined or (raw_words >= 6 and joined_words < int(raw_words * 0.65)):
            logger.warning(
                "assembled cleanup dropped too much (%d→%d words), using raw",
                raw_words, joined_words,
            )
            return raw
        return joined

    # ── delivery ─────────────────────────────────────────────────────────

    def _deliver(
        self, text: str, raw: str, samples: np.ndarray, result: Result, key_up: float,
        started: float, stt_ms: float, cleanup_ms: float, target_window: int,
        retried: bool = False, stream_passes: int = 0, shadow=None,
        pre_cleaned_words: int | None = None,
    ) -> None:
        """Deliver the final transcript.

        ORDER MATTERS for reliability: the text is saved to History FIRST (the
        vault — it can never be lost from here on), then routed to the focused
        app. Every path keeps the text recoverable; nothing is ever silently
        discarded.
        """
        if not text:
            return
        # Before anything else sees the text, so History, the result panel and
        # the typed output all agree on what was said.
        if profiles.get_bool("FVVoiceCommands", winapi.foreground_app_name()):
            text = apply_voice_commands(text)
            if not text:
                return
        audio_seconds = samples.size / SAMPLE_RATE
        self.last_transcript = text
        self._last_dictation_at = time.monotonic()

        # ── Vault: persist before any step that could fail. Keep the raw
        # transcript too when cleanup changed it, so the exact spoken words are
        # always recoverable even if the LLM mis-edited. ──
        self.store.add(
            DictationRecord(
                date=metrics.now(), words=word_count(text), duration=audio_seconds,
                app=winapi.foreground_app_name(), transcript=text,
                raw=None if raw == text else raw,
            )
        )

        total_ms = (time.monotonic() - key_up) * 1000
        if config.get_bool("FVDebugTimings"):
            logger.info(
                "timings capture=%.1fs stt=%.0fms cleanup=%.0fms total=%.0fms "
                "logprob=%.2f chars=%d",
                audio_seconds, stt_ms, cleanup_ms, total_ms, result.avg_logprob, len(text),
            )
        else:
            logger.info("key-up → typed %.0f ms (%d chars)", total_ms, len(text))

        # Focus guard: a transient popup can steal focus and hand it back, so
        # recheck once after a short settle before deciding.
        current = winapi.foreground_window()
        focus_held = target_window == 0 or current == target_window
        if not focus_held:
            time.sleep(0.5)
            focus_held = winapi.foreground_window() == target_window

        stale = time.monotonic() - started >= self.STALE_PASTE_LIMIT
        editable = injector.focused_element_is_editable()

        route = decide_delivery(stale=stale, focus_held=focus_held, editable=editable)
        if route == "type":
            if injector.inject(text):
                outcome = "pasted" if editable is True else "pasted-blind"
                logger.info("outcome = typed (editable = %s)",
                            "yes" if editable else "unknown")
                sound.play("success")
            else:
                outcome = "panel-failed"
                self._hold(t("hold.failed"), text)
        else:
            outcome = f"panel-{route}"
            # A stale transcript gets no success tone: the user has moved on,
            # and a chime for something that did NOT reach their cursor reads
            # as "done" when it is not.
            if route != "stale":
                sound.play("success")
            self._hold(t(f"hold.{route}"), text)

        metrics.record(
            metrics.DictationMetric(
                date=metrics.now(), capture_sec=audio_seconds, stt_ms=stt_ms,
                cleanup_ms=cleanup_ms, total_ms=total_ms, chars=len(text),
                outcome=outcome,
                # < 20 ms means no LLM round-trip happened (disabled, or
                # needs_cleanup said the text was already clean) — an HTTP call
                # is never that fast.
                cleanup_skipped=cleanup_ms < 20, retried=retried,
                stream_passes=stream_passes, logprob=result.avg_logprob,
                model=self.transcriber.loaded_model or "?",
                device=self.transcriber.device,
                cleanup_chunks=self.cleaner.last_chunk_count or None,
                cleanup_skipped_sentences=self.cleaner.last_skipped_sentences or None,
                shadow_diff_words=shadow.diff_words if shadow else None,
                stitched_ms=shadow.stitched_ms if shadow else None,
                batch_ms=shadow.batch_ms if shadow else None,
                pre_cleaned_words=pre_cleaned_words,
                cleanup_over_budget=self.cleaner.last_over_budget_chunks or None,
            )
        )

    def _hold(self, reason: str, text: str) -> None:
        """We couldn't type it — show the transcript with a Copy button.

        Nothing is put on the clipboard until the user presses that button.
        That is the whole reason the macOS build stopped routing dictations
        through the clipboard, and a panel that pre-copied would undo it.
        """
        logger.info("outcome = held (%s)", reason)
        if self.ui_available:
            from .ui.result import panel

            panel.show(text, reason)
            return
        preview = text if len(text) <= 120 else text[:117] + "…"
        self._notify(reason, preview)

    def _record_drop(
        self, outcome: str, text: str, audio_seconds: float, stt_ms: float,
        key_up: float, stream_passes: int, result: Result,
    ) -> None:
        metrics.record(
            metrics.DictationMetric(
                date=metrics.now(), capture_sec=audio_seconds, stt_ms=stt_ms,
                cleanup_ms=0, total_ms=(time.monotonic() - key_up) * 1000,
                chars=len(text), outcome=outcome, cleanup_skipped=True, retried=False,
                stream_passes=stream_passes, logprob=result.avg_logprob,
                model=self.transcriber.loaded_model or "?",
                device=self.transcriber.device,
            )
        )

    # ── recovery ─────────────────────────────────────────────────────────

    def recover_failed(self) -> None:
        """Re-decode the newest failed recording; success lands in History."""
        if self.state != State.IDLE:
            return
        pending = self.recovery.pending()
        if not pending:
            return
        path = pending[-1]
        samples = self.recovery.load(path)
        if samples is None:
            return
        self._set_state(State.PROCESSING)

        def run() -> None:
            try:
                timeout = max(self.TRANSCRIBE_TIMEOUT, samples.size / SAMPLE_RATE * 3)
                result = run_with_timeout(
                    timeout, lambda: self.transcriber.transcribe(samples)
                )
                if not result.text:
                    sound.play("error")  # still nothing — keep the WAV for later
                    return
                self.last_transcript = result.text
                self.store.add(
                    DictationRecord(
                        date=metrics.now(), words=word_count(result.text),
                        duration=samples.size / SAMPLE_RATE, app=None,
                        transcript=result.text,
                    )
                )
                self.recovery.delete(path)
                sound.play("success")
                self._notify(t("notify.recovered"), result.text[:120])
            except Exception as exc:  # noqa: BLE001 - the WAV stays for another attempt
                logger.error("recovery decode failed: %s", exc)
                sound.play("error")
            finally:
                self._set_state(State.IDLE)

        threading.Thread(target=run, name="recover", daemon=True).start()

    def copy_last(self) -> bool:
        if not self.last_transcript:
            return False
        return injector.set_clipboard_text(self.last_transcript)

    # Long enough for the tray menu to close and Windows to hand the
    # foreground back to whatever was underneath it. Typing before that puts
    # the text into a menu that is still closing, which is to say nowhere.
    RETYPE_DELAY = 0.35

    def retype_last(self) -> bool:
        """Type the last dictation into whatever has focus now.

        The rescue for a dictation that landed in the wrong window: 17 of this
        user's first 20 deliveries were typed "blind", because Windows would
        not confirm there was an editable field, so a miss is entirely
        possible and until now the only recovery was to copy and paste by
        hand.
        """
        if not self.last_transcript:
            return False
        text = self.last_transcript

        def job() -> None:
            time.sleep(self.RETYPE_DELAY)
            injector.release_held_modifiers()
            if not injector.inject(text):
                logger.warning("retype failed, leaving it on the clipboard")
                injector.set_clipboard_text(text)

        threading.Thread(target=job, name="retype", daemon=True).start()
        return True

    def open_data_folder(self) -> None:
        import os

        os.startfile(str(paths.home()))  # noqa: S606 - opening our own folder
