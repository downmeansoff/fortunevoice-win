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
        def __init__(self, name, device, compute_type, download_root, num_workers):
            key = f"{device}/{compute_type}"
            built.append({"name": name, "device": device,
                          "compute_type": compute_type, "root": download_root,
                          "workers": num_workers})
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
        ("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")]


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
    assert Transcriber()._backends()[0] == ("cuda", "float16")


# ── loading ──────────────────────────────────────────────────────────────


def test_the_first_working_backend_wins(fake_whisper):
    config.set("FVDevice", "auto")
    engine = Transcriber()
    engine.load()
    assert engine.device == "cuda"
    assert engine.compute_type == "float16"
    assert len(fake_whisper.built) == 1, "no backend should be tried after one works"


def test_a_refused_backend_moves_on_to_the_next(fake_whisper):
    """What a machine with no CUDA actually does."""
    config.set("FVDevice", "auto")
    fake_whisper.fails.update({"cuda/float16", "cuda/int8_float16"})
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
    assert "cuda/float16" in message and "cpu/int8" in message


def test_a_missing_faster_whisper_says_how_to_fix_it(monkeypatch):
    monkeypatch.setattr(transcriber, "_add_cuda_dll_directories", lambda: None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(TranscriberError, match="pip install"):
        Transcriber().load()


def test_only_one_ctranslate_worker(fake_whisper):
    """The gate already serialises decoding, and a second worker doubles VRAM
    for nothing — on a 6 GB card that is the difference between fitting beside
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
    """Otherwise one failed decode wedges every dictation after it — the app
    would look alive and never transcribe again."""
    engine = Transcriber()

    class Exploding:
        def transcribe(self, audio, **options):
            raise RuntimeError("CUDA out of memory")

    engine._model = Exploding()
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
    sends the decoder into repeated re-decodes — making the warmup slower than
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
