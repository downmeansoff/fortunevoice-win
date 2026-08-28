"""The whole chain, with a real voice and the real model.

Every other test in this suite stubs the model: they check the app's ordering
and its guards, and they are fast because nothing decodes. That leaves the one
question they cannot answer — does a person speaking actually end up with their
words typed into the window in front of them?

So this synthesises Russian speech with the voice Windows ships (SAPI, Irina),
feeds the audio through the real `Transcriber` on the real GPU, and asserts the
text comes out of the real pipeline. It is slow and it needs the model on disk,
so it is opt-in:

    pytest -m e2e

The three cases here are the ones that were broken and could only be verified
by dictating: a dictation after the idle unload dropped the model, a model
changed in Settings, and a long phrase in toggle mode.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows only"),
]

SAMPLE_RATE = 16_000
PHRASE = "Проверь пожалуйста последний коммит там поменялась логика подписки"

_SAY = """param([string]$Text, [string]$Out)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ru-RU' } | Select-Object -First 1
if (-not $voice) { exit 3 }
$synth.SelectVoice($voice.VoiceInfo.Name)
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($Out, $format)
$synth.Speak($Text)
$synth.Dispose()
"""


@pytest.fixture(scope="session")
def spoken() -> np.ndarray:
    """`PHRASE`, spoken by the Russian voice Windows ships, as float32 mono."""
    folder = Path(tempfile.mkdtemp(prefix="fv-e2e-"))
    script, wav = folder / "say.ps1", folder / "phrase.wav"
    script.write_text(_SAY, encoding="utf-8-sig")

    done = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(script), "-Text", PHRASE, "-Out", str(wav)],
        capture_output=True, text=True,
    )
    if done.returncode == 3:
        pytest.skip("no Russian SAPI voice installed")
    assert done.returncode == 0, done.stderr
    assert wav.exists() and wav.stat().st_size > 20_000, "the voice produced nothing"

    with wave.open(str(wav), "rb") as handle:
        assert handle.getframerate() == SAMPLE_RATE
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


@pytest.fixture(autouse=True)
def real_model(monkeypatch):
    """Undo the suite-wide ban on loading a model — this file is about the
    model — and point the weights at the ones already on disk.

    Only the weights: history, config and metrics stay in the per-test temp
    home, so running this never touches what the user has dictated. Without
    the override the isolated home has no models directory and the first case
    tries to download two gigabytes.
    """
    import os

    from fortunevoice import paths
    from fortunevoice.transcriber import Transcriber

    monkeypatch.setattr(Transcriber, "_load_locked", Transcriber._load_locked)

    appdata = os.environ.get("APPDATA")
    if not appdata:
        pytest.skip("no APPDATA to find the downloaded weights in")
    weights = Path(appdata) / "FortuneVoice" / "models"
    if not weights.exists():
        pytest.skip("no model on disk — run the app once first")
    monkeypatch.setattr(paths, "models_dir", lambda: weights)


class SpeaksOnce:
    """A recorder that hands over one recording, like a microphone would."""

    def __init__(self, samples):
        self.samples = samples
        self.is_recording = False
        self.starts = 0
        self.stops = 0
        self.on_level = None

    def start(self, device: str = "") -> None:
        self.starts += 1
        self.is_recording = True

    def stop(self):
        self.stops += 1
        self.is_recording = False
        return self.samples

    def snapshot(self):
        return self.samples


@pytest.fixture
def app(monkeypatch, spoken):
    """A real App — real transcriber, real cleaner — with the microphone and
    the keyboard replaced, and nothing allowed to reach the user's desktop."""
    from fortunevoice import app as app_module
    from fortunevoice.app import App, State

    application = App()
    application.recorder = SpeaksOnce(spoken)

    typed: list[str] = []
    monkeypatch.setattr(app_module.injector, "inject",
                        lambda text: typed.append(text) or True)
    monkeypatch.setattr(app_module.injector, "focused_element_is_editable",
                        lambda: True)
    monkeypatch.setattr(app_module.sound, "play", lambda name: None)
    monkeypatch.setattr(app_module.winapi, "foreground_window", lambda: 4242)
    monkeypatch.setattr(app_module.winapi, "foreground_app_name", lambda: "Code.exe")
    application.typed = typed
    application._set_state(State.IDLE)
    return application


def _speak_and_wait(app, seconds: float = 90.0) -> str:
    """Drive one dictation the way the hotkey does, and wait for the words."""
    from fortunevoice.app import State

    app._start_dictation()
    assert app.state is State.RECORDING, "the recording never started"
    app._finish_dictation(time.monotonic(), 4242)

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if app.typed:
            return app.typed[-1]
        time.sleep(0.1)
    raise AssertionError(f"nothing was typed in {seconds:.0f}s (state {app.state})")


def _sounds_right(text: str) -> bool:
    """Whisper will not match the phrase word for word — it punctuates and
    sometimes hears a different ending. Check the words that carry it."""
    lowered = text.lower()
    # Not "коммит": the synthetic voice says it in a way Whisper reliably hears
    # as "коммент", which is a fair mishearing and not the app being wrong.
    return all(word in lowered
               for word in ("проверь", "поменялась", "логика", "подписк"))


def test_a_dictation_arrives(app):
    """The baseline: speech in, text out, through everything."""
    typed = _speak_and_wait(app)
    assert _sounds_right(typed), typed


def test_a_dictation_after_the_idle_unload_still_arrives(app):
    """The one that lost words.

    With FVUnloadModelAfter set, the idle watcher drops the model; the next
    key-down starts a reload underneath the recording. That reload announced
    LOADING and then IDLE while the user was still speaking, and `_stop_dictation`
    returns early unless the state is RECORDING — so at key-up nothing was
    decoded, nothing typed, nothing saved, and no sound played.
    """
    from fortunevoice.app import State

    app.transcriber.load()
    assert app.transcriber.is_loaded
    assert app.transcriber.unload(), "precondition: the model has to be dropped"

    typed = _speak_and_wait(app)

    assert _sounds_right(typed), typed
    assert app.state is State.IDLE
    assert [r.transcript for r in app.store.all()], "and it reached History"


def test_changing_the_model_actually_changes_it(app):
    """`Transcriber.load()` is a no-op while a model is resident, so a reload
    that did not unload first did nothing at all: choosing a different model in
    Settings went on using the old one until the app was restarted."""
    import threading

    from fortunevoice import config

    config.set("FVModel", "small")
    app.transcriber.load()
    first = app.transcriber.loaded_model

    config.set("FVModel", "large-v3-turbo")
    app.reload_model()
    for thread in threading.enumerate():
        if thread.name == "model-reload":
            thread.join(180)

    assert app.transcriber.is_loaded
    assert app.transcriber.loaded_model != first, "the new model never loaded"
    assert _sounds_right(_speak_and_wait(app)), "and it still transcribes"


def test_a_long_phrase_is_not_cut_off_in_toggle_mode(app, spoken):
    """The auto-stop treated anything under 0.2 RMS as silence — ten times the
    app's own floor for speech — so a normal voice never counted as talking and
    toggle mode ended the recording four seconds in, mid-sentence."""
    from fortunevoice.app import State

    app._set_state(State.RECORDING)
    app._peak_level = 0.0
    app._last_loud = 0.0

    block = SAMPLE_RATE // 16          # 62 ms, the size the recorder reports
    heard = 0
    for start in range(0, spoken.size - block, block):
        chunk = spoken[start:start + block]
        level = float(np.sqrt(np.mean(np.square(chunk))))
        app._on_level(level)
        if app._last_loud:
            heard += 1
            app._last_loud = 0.0       # reset, so each block is judged alone

    assert heard > 0, "not one block of real speech counted as speech"
    speech_blocks = heard / (spoken.size // block)
    assert speech_blocks > 0.25, (
        f"only {speech_blocks:.0%} of the phrase counted as speech — the "
        "silence timer would fire in the middle of it")
