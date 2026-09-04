"""Loading the model and decoding with it.

The largest module with no tests at all, and the one whose failure mode is
total: if the backend ladder or the decode options break, the app stops
producing text and every other test stays green.

No GPU and no real model. `load()` imports `faster_whisper` inside the
function, so a fake module in `sys.modules` is enough to drive the whole
ladder, and `transcribe()` only needs an object with a `.transcribe`.
"""

from __future__ import annotations

import sys
import threading
import time
import types

import numpy as np
import pytest

from fortunevoice import config, transcriber
from fortunevoice.transcriber import Result, Transcriber, TranscriberError


class FakeSegment:
    def __init__(self, text, start=0.0, end=1.0, avg_logprob=-0.2, no_speech_prob=0.1):
        self.text = text
        self.start = start
        self.end = end
        self.avg_logprob = avg_logprob
        self.no_speech_prob = no_speech_prob


class FakeModel:
    """Records the options it was decoded with, and returns fixed segments."""

    def __init__(self, segments=(), language="ru"):
        self.segments = list(segments)
        self.language = language
        self.calls: list[dict] = []

    def transcribe(self, audio, **options):
        self.calls.append({"audio": audio, **options})
        info = types.SimpleNamespace(language=self.language)
        return iter(self.segments), info


@pytest.fixture
def fake_whisper(monkeypatch):
    """A stand-in `faster_whisper` module whose WhisperModel is scriptable.

    `fails` maps "device/compute_type" to True for the backends that should
    refuse, which is how a machine without CUDA behaves.
    """
    built: list[dict] = []
    fails: set[str] = set()

    class WhisperModel:
        def __init__(self, name, device, compute_type, download_root,
                     num_workers, **options):
            # **options, so a new engine parameter is a change in one
            # place rather than seven red tests about a keyword the fake
            # does not know.
            key = f"{device}/{compute_type}"
            built.append({"name": name, "device": device,
                          "compute_type": compute_type, "root": download_root,
                          "workers": num_workers, **options})
            if key in fails or name in fails:
                raise RuntimeError(f"{key} unavailable")
            self.name = name

        def transcribe(self, audio, **options):
            return iter(()), types.SimpleNamespace(language="ru")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    # The CUDA DLL search-path setup touches the real filesystem and is not
    # what any of this is about.
    monkeypatch.setattr(transcriber, "_add_cuda_dll_directories", lambda: None)
    return types.SimpleNamespace(built=built, fails=fails)


# ── the backend ladder ───────────────────────────────────────────────────


def test_auto_prefers_cuda_then_falls_back_to_cpu():
    config.set("FVDevice", "auto")
    assert Transcriber()._backends() == [
        ("cuda", "int8_float16"), ("cuda", "float16"), ("cpu", "int8")]


def test_cpu_is_taken_literally():
    config.set("FVDevice", "cpu")
    assert Transcriber()._backends() == [("cpu", "int8")]


def test_cuda_still_falls_back_to_cpu():
    """A dead CUDA runtime beats no dictation. Asking for cuda and getting
    nothing when the runtime is broken would be the app refusing to work on a
    machine that can perfectly well work, slowly."""
    config.set("FVDevice", "cuda")
    assert Transcriber()._backends()[-1] == ("cpu", "int8")


def test_an_unknown_device_behaves_like_auto():
    config.set("FVDevice", "quantum")
    assert Transcriber()._backends()[0] == ("cuda", "int8_float16")


# ── loading ──────────────────────────────────────────────────────────────


def test_the_first_working_backend_wins(fake_whisper):
    config.set("FVDevice", "auto")
    engine = Transcriber()
    engine.load()
    assert engine.device == "cuda"
    assert engine.compute_type == "int8_float16"
    assert len(fake_whisper.built) == 1, "no backend should be tried after one works"


def test_a_refused_backend_moves_on_to_the_next(fake_whisper):
    """What a machine with no CUDA actually does."""
    config.set("FVDevice", "auto")
    fake_whisper.fails.update({"cuda/int8_float16", "cuda/float16"})
    engine = Transcriber()
    engine.load()
    assert (engine.device, engine.compute_type) == ("cpu", "int8")


def test_the_fallback_model_is_tried_when_every_backend_fails(fake_whisper):
    config.set("FVModel", "large-v3-turbo")
    config.set("FVFallbackModel", "small")
    fake_whisper.fails.add("large-v3-turbo")
    engine = Transcriber()
    engine.load()
    assert engine.loaded_model == "small (fallback)"


def test_the_fallback_is_labelled_so_the_user_can_tell(fake_whisper):
    """Insights and the doctor report this name. A silently-degraded model
    that reports the wanted one is worse than no report at all."""
    config.set("FVModel", "large-v3-turbo")
    config.set("FVFallbackModel", "small")
    engine = Transcriber()
    engine.load()
    assert engine.loaded_model == "large-v3-turbo", "not a fallback, so no label"


def test_a_fallback_equal_to_the_model_is_not_tried_twice(fake_whisper):
    config.set("FVModel", "small")
    config.set("FVFallbackModel", "small")
    fake_whisper.fails.add("small")
    with pytest.raises(TranscriberError):
        Transcriber().load()
    names = {b["name"] for b in fake_whisper.built}
    assert names == {"small"}
    assert len(fake_whisper.built) == 3, "three backends, one model, no repeats"


def test_nothing_loading_raises_with_every_reason(fake_whisper):
    """The message is what the doctor and the tray show. "It didn't work" with
    no reasons is what makes this class of problem unfixable by the user."""
    config.set("FVModel", "large-v3-turbo")
    config.set("FVFallbackModel", "small")
    fake_whisper.fails.update({"large-v3-turbo", "small"})
    with pytest.raises(TranscriberError) as caught:
        Transcriber().load()
    message = str(caught.value)
    assert "large-v3-turbo" in message and "small" in message
    assert "cuda/int8_float16" in message and "cpu/int8" in message


def test_a_missing_faster_whisper_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(transcriber, "_add_cuda_dll_directories", lambda: None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(TranscriberError, match="pip install"):
        Transcriber().load()


def test_only_one_ctranslate_worker(fake_whisper):
    """The gate already serialises decoding, and a second worker doubles VRAM
    for nothing; on a 6 GB card that is the difference between fitting beside
    the cleanup model and not."""
    Transcriber().load()
    assert fake_whisper.built[0]["workers"] == 1


def test_the_model_is_downloaded_into_the_app_folder(fake_whisper):
    from fortunevoice import paths

    Transcriber().load()
    assert fake_whisper.built[0]["root"] == str(paths.models_dir())


# ── decoding ─────────────────────────────────────────────────────────────


@pytest.fixture
def loaded():
    engine = Transcriber()
    model = FakeModel([FakeSegment("привет "), FakeSegment(" как дела")])
    engine._model = model
    return engine, model


def test_decoding_before_the_model_is_loaded_is_an_error():
    with pytest.raises(TranscriberError, match="not loaded"):
        Transcriber().transcribe(np.zeros(16_000, dtype=np.float32))


def test_empty_audio_returns_empty_without_touching_the_model(loaded):
    engine, model = loaded
    assert engine.transcribe(np.zeros(0, dtype=np.float32)) == Result(text="")
    assert model.calls == []


def test_segments_are_joined_and_squeezed(loaded):
    engine, _ = loaded
    assert engine.transcribe(np.zeros(1600, dtype=np.float32)).text == "привет как дела"


def test_confidence_is_averaged_over_the_segments():
    engine = Transcriber()
    engine._model = FakeModel([
        FakeSegment("раз", avg_logprob=-0.2, no_speech_prob=0.0),
        FakeSegment("два", avg_logprob=-0.6, no_speech_prob=0.4),
    ])
    result = engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert result.avg_logprob == pytest.approx(-0.4)
    assert result.no_speech_prob == pytest.approx(0.2)
    # A confident decode must not read as garbled: the app rewrites words
    # below -0.9, and a wrong average here would let it rewrite good text.
    assert result.avg_logprob > -0.9


def test_no_segments_does_not_divide_by_zero():
    engine = Transcriber()
    engine._model = FakeModel([])
    result = engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert result.text == ""
    assert result.avg_logprob == 0.0


def test_a_forced_language_is_passed_through(loaded):
    engine, model = loaded
    config.set("FVLanguage", "ru")
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert model.calls[0]["language"] == "ru"


def test_auto_detects_once_and_then_reuses_it(loaded):
    """Detection is unstable under ~3 s. Re-deriving it on every streaming
    pass is how a dictation ends up half in one language and half in another."""
    engine, model = loaded
    config.set("FVLanguage", "auto")
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert model.calls[0]["language"] is None, "nothing detected yet"
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert model.calls[1]["language"] == "ru", "reuses what this dictation found"


def test_resetting_forgets_the_detected_language(loaded):
    engine, model = loaded
    config.set("FVLanguage", "auto")
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    engine.reset_session_language()
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert model.calls[1]["language"] is None, "the user may have switched languages"


def test_the_custom_vocabulary_biases_the_decoder(loaded):
    from fortunevoice import dictionary

    engine, model = loaded
    dictionary.set_terms(["Hetzner", "Xray"])
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert "Hetzner" in model.calls[0]["initial_prompt"]


def test_the_decoding_options_that_were_paid_for_with_bugs(loaded):
    """Each of these has a comment in the source naming the failure it
    prevents. They are the kind of thing a later "simplification" removes."""
    engine, model = loaded
    engine.transcribe(np.zeros(1600, dtype=np.float32))
    options = model.calls[0]

    assert options["temperature"] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0], (
        "temperature fallback re-decodes windows that fail the checks; "
        "removing it is why long dictations used to lose text")
    assert options["condition_on_previous_text"] is False, (
        "feeding the previous window back is the biggest amplifier of "
        "repetition loops in faster-whisper")
    assert options["compression_ratio_threshold"] == 2.0, (
        "lower than the 2.4 default so repetition hallucinations are rejected")
    assert options["beam_size"] == 1, "beam search costs latency this loop cannot spare"
    assert options["vad_filter"] is False, (
        "a second VAD would move the timestamps the streaming session cuts on")


def test_the_gate_is_released_even_when_the_decode_raises():
    """Otherwise one failed decode wedges every dictation after it: the app
    would look alive and never transcribe again.

    A plain error, deliberately: "CUDA out of memory" is now the signal to
    rebuild the model, and this test is about the gate, not about the GPU.
    """
    engine = Transcriber()

    class Exploding:
        def transcribe(self, audio, **options):
            raise RuntimeError("the decoder gave up")

    engine._model = Exploding()
    with pytest.raises(RuntimeError):
        engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert engine._gate.acquire(1.0), "the gate must not be left held"
    engine._gate.release()


def test_the_gate_survives_a_rebuild_after_a_dead_context(monkeypatch):
    """The rebuild path acquires and releases the gate twice: once for the
    decode that died, once for the retry. Getting that wrong wedges the app in
    exactly the situation it exists to rescue."""
    engine = Transcriber()

    class Exploding:
        def transcribe(self, audio, **options):
            raise RuntimeError("cuBLAS failed with status CUBLAS_STATUS_INTERNAL_ERROR")

    engine._model = Exploding()
    # No real load: this module is exempt from the no-network guard, and a
    # rebuild here would fetch Whisper from Hugging Face.
    monkeypatch.setattr(engine, "load", lambda: None)

    with pytest.raises(RuntimeError):
        engine.transcribe(np.zeros(1600, dtype=np.float32))

    assert engine._gate.acquire(1.0), "the gate must not be left held"
    engine._gate.release()


# ── warm-up ──────────────────────────────────────────────────────────────


def wait_for(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_warmup_does_nothing_without_a_model():
    engine = Transcriber()
    engine.warmup()  # must not raise, must not start a thread
    assert engine._warmup_thread is None


def test_warmup_is_skipped_while_the_pipeline_is_still_hot(loaded):
    engine, model = loaded
    engine._last_decode_at = time.monotonic()
    engine.warmup()
    assert model.calls == [], "a warm pipeline only burns power"


def test_warmup_runs_after_an_idle_gap(loaded):
    engine, model = loaded
    engine._last_decode_at = time.monotonic() - engine.WARM_IDLE_THRESHOLD - 1
    engine.warmup()
    assert wait_for(lambda: model.calls), "an idle pipeline needs spinning up"


def test_warmup_uses_the_simple_options_not_the_real_ones(loaded):
    """Silence can fail the compression check, and with the real options that
    sends the decoder into repeated re-decodes, making the warmup slower than
    the dictation it exists to speed up."""
    engine, model = loaded
    engine._last_decode_at = None
    engine.warmup()
    assert wait_for(lambda: model.calls)
    options = model.calls[0]
    assert options["temperature"] == [0.0], "no temperature fallback"
    assert options.get("initial_prompt") is None, "no custom vocabulary"
    assert options["language"] == "en"


def test_two_warmups_do_not_run_at_once(loaded):
    engine, model = loaded
    engine._last_decode_at = None
    started = threading.Event()
    release = threading.Event()

    def slow(audio, **options):
        model.calls.append(options)
        started.set()
        release.wait(2.0)
        return iter(()), types.SimpleNamespace(language="ru")

    model.transcribe = slow
    engine.warmup()
    assert started.wait(2.0)
    engine.warmup()          # while the first is still inside the decode
    release.set()
    assert wait_for(lambda: not engine._warmup_in_flight)
    assert len(model.calls) == 1


def test_a_warmup_marks_the_pipeline_warm(loaded):
    engine, model = loaded
    engine._last_decode_at = None
    engine.warmup()
    assert wait_for(lambda: model.calls)
    assert wait_for(lambda: engine._last_decode_at is not None)


def test_cancel_warmup_is_safe_when_none_is_running():
    Transcriber().cancel_warmup()  # must not raise or block


def test_a_failing_warmup_is_swallowed(loaded):
    """Best-effort work must never surface as a failed dictation."""
    engine, model = loaded
    engine._last_decode_at = None

    def explode(audio, **options):
        raise RuntimeError("no VRAM for a warmup")

    model.transcribe = explode
    engine.warmup()
    assert wait_for(lambda: not engine._warmup_in_flight)
    assert engine._gate.acquire(1.0), "the gate must not be left held"
    engine._gate.release()


# ── giving the video memory back ─────────────────────────────────────────


def test_unloading_nothing_reports_nothing():
    assert Transcriber().unload() is False


def test_unloading_clears_the_model_and_what_is_reported(loaded):
    """Insights and the doctor read these. Leaving them set would report a
    model that is no longer resident."""
    engine, _ = loaded
    assert engine.is_loaded is True
    assert engine.unload() is True
    assert engine.is_loaded is False
    assert engine.loaded_model is None
    assert engine.device is None


def test_a_decode_in_progress_is_not_unloaded_from_under_it(loaded):
    """Measured: this frees ~1.9 GB. Doing it mid-transcription would take the
    user's dictation with it."""
    engine, _ = loaded
    assert engine._gate.acquire(1.0), "stand in for a decode holding the gate"
    try:
        assert engine.unload() is False
        assert engine.is_loaded is True
    finally:
        engine._gate.release()


def test_unloading_lets_the_next_load_start_clean(loaded, fake_whisper):
    engine, _ = loaded
    engine.unload()
    engine.load()
    assert engine.is_loaded is True
    assert engine.device is not None


def test_two_threads_loading_at_once_build_one_model(fake_whisper):
    """A dictation reloads a model the idle unload dropped, from two places:
    a background thread at key-down and the pipeline itself at key-up. Two
    WhisperModels on the same card is 2 GB each here, and the loser's is
    dropped on the floor after being paid for."""
    import threading

    engine = Transcriber()
    barrier = threading.Barrier(4)

    def race():
        barrier.wait()
        engine.load()

    threads = [threading.Thread(target=race) for _ in range(3)]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join(timeout=5)

    assert len(fake_whisper.built) == 1, "one model, however many callers"
    assert engine.is_loaded is True


def test_loading_an_already_loaded_model_is_a_no_op(fake_whisper, loaded):
    engine, _ = loaded
    engine.load()
    assert fake_whisper.built == [], "nothing was built"


# ── a dead GPU context is a broken model, not a lost dictation ───────────


def test_a_cublas_failure_rebuilds_the_model_and_retries(monkeypatch):
    """Seen live: cuBLAS_STATUS_INTERNAL_ERROR inside model.encode, three
    dictations in a row, with 3 GB of the card free. The context does not
    heal, the model object is still "loaded" so nothing reloads it, and every
    dictation after it is lost to "FortuneVoice couldn't transcribe that". The
    only cure was quitting from the tray."""
    import numpy as np

    from fortunevoice.transcriber import Result, Transcriber

    transcriber = Transcriber()
    transcriber._model = object()
    attempts = []
    loads = []

    def flaky(samples):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("cuBLAS failed with status CUBLAS_STATUS_INTERNAL_ERROR")
        return Result(text="дошло со второго раза")

    monkeypatch.setattr(transcriber, "_transcribe_once", flaky)
    monkeypatch.setattr(transcriber, "unload",
                        lambda force=False: loads.append(("unload", force)) or True)
    monkeypatch.setattr(transcriber, "load", lambda: loads.append(("load", None)))

    result = transcriber.transcribe(np.zeros(16_000, dtype=np.float32))

    assert result.text == "дошло со второго раза"
    assert len(attempts) == 2, "it has to try again after rebuilding"
    assert loads == [("unload", True), ("load", None)], loads


def test_an_ordinary_failure_is_not_retried(monkeypatch):
    """Only a dead context earns a rebuild. Retrying everything would decode
    twice on every real error and double the wait before the user is told."""
    import numpy as np
    import pytest as _pytest

    from fortunevoice.transcriber import Transcriber

    transcriber = Transcriber()
    transcriber._model = object()
    attempts = []

    def broken(samples):
        attempts.append(1)
        raise ValueError("the audio made no sense")

    monkeypatch.setattr(transcriber, "_transcribe_once", broken)
    monkeypatch.setattr(transcriber, "unload",
                        lambda force=False: _pytest.fail("nothing to rebuild"))

    with _pytest.raises(ValueError):
        transcriber.transcribe(np.zeros(16_000, dtype=np.float32))
    assert len(attempts) == 1


def test_the_second_failure_is_reported_rather_than_looped(monkeypatch):
    """A card that is genuinely out of memory fails the rebuild too. One
    retry, then the truth; the audio is kept either way."""
    import numpy as np
    import pytest as _pytest

    from fortunevoice.transcriber import Transcriber

    transcriber = Transcriber()
    transcriber._model = object()
    attempts = []

    def always_dead(samples):
        attempts.append(1)
        raise RuntimeError("CUDA failed with error out of memory")

    monkeypatch.setattr(transcriber, "_transcribe_once", always_dead)
    monkeypatch.setattr(transcriber, "unload", lambda force=False: True)
    monkeypatch.setattr(transcriber, "load", lambda: None)

    with _pytest.raises(RuntimeError):
        transcriber.transcribe(np.zeros(16_000, dtype=np.float32))
    assert len(attempts) == 2, "exactly one retry"


def test_a_full_card_is_not_a_dead_context(monkeypatch):
    """A card that is merely full -- a game started, another model loaded --
    reports out of memory too. Rebuilding then walks the backend ladder and
    can land the app on the CPU for the rest of the session: a permanent
    penalty for a temporary condition. The failure actually seen here was
    CUBLAS_STATUS_INTERNAL_ERROR with 3 GB free."""
    import numpy as np
    import pytest as _pytest

    from fortunevoice.transcriber import Transcriber

    engine = Transcriber()
    engine._model = object()
    attempts = []

    def full(samples):
        attempts.append(1)
        # The message a full card actually gives, observed here when
        # Whisper was resident and a second model tried to load.
        # "cuda failed" stays a dead-context marker; a malloc that could
        # not find room is not one.
        raise RuntimeError("cudaMalloc failed: out of memory")

    monkeypatch.setattr(engine, "_transcribe_once", full)
    monkeypatch.setattr(engine, "unload",
                        lambda force=False: _pytest.fail("nothing to rebuild"))

    with _pytest.raises(RuntimeError):
        engine.transcribe(np.zeros(1600, dtype=np.float32))
    assert len(attempts) == 1


def test_a_rebuild_leaves_another_threads_model_alone(monkeypatch):
    """Between the exception and the rebuild another thread may have replaced
    the model already, or started a decode on a perfectly good one. Forcing
    the gate away from that breaks a dictation that was going fine to fix one
    that is already over."""
    import numpy as np
    import pytest as _pytest

    from fortunevoice.transcriber import Result, Transcriber

    engine = Transcriber()
    broken = object()
    engine._model = broken
    calls = []

    def died(samples):
        calls.append(1)
        if len(calls) == 1:
            # Somebody else rebuilt it while we were failing.
            engine._model = object()
            raise RuntimeError("cuBLAS failed with status CUBLAS_STATUS_INTERNAL_ERROR")
        return Result(text="уже здоровая модель")

    monkeypatch.setattr(engine, "_transcribe_once", died)
    monkeypatch.setattr(engine, "unload",
                        lambda force=False: _pytest.fail("that model is not ours"))
    monkeypatch.setattr(engine, "load", lambda: _pytest.fail("nor is the rebuild"))

    assert engine.transcribe(np.zeros(1600, dtype=np.float32)).text ==         "уже здоровая модель"
