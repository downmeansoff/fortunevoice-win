# FortuneVoice for Windows

Local, offline voice dictation. Hold a hotkey, speak, release — the
transcribed text is typed into whatever app has focus. Audio never leaves the
machine.

A port of [FortuneVoice](https://github.com/thatluckyoldsun/FortuneVoice)
(macOS/Swift) to Windows. The macOS build runs on WhisperKit and CoreML and
cannot run here at all; this is a rebuilt client that keeps the original's
behaviour — its latency pipeline, its safety nets, its tuning constants and
its tests. See [PORT_NOTES.md](PORT_NOTES.md) for what carried over and what
had to be redone.

## How it works

```
Ctrl+Alt+Space (hold) → mic capture (16 kHz) → faster-whisper (local STT, CUDA)
                      → [optional] Ollama gemma3:4b cleanup → typed into the focused app
```

- **STT:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) —
  CTranslate2, CUDA-accelerated. The model downloads on first launch (default:
  `large-v3-turbo`).
- **Streaming:** the transcript is decoded *while you speak*, so releasing the
  key costs roughly the time of your last few seconds, not of the whole
  utterance.
- **AI cleanup (optional):** a local [Ollama](https://ollama.com) model removes
  filler words and fixes punctuation. Falls back to raw text whenever Ollama is
  slow, down, or drops content.
- **Injection:** typed directly into the focused field as synthesized key
  events. The clipboard is never touched — the only thing that reaches it is
  what you copy yourself from the tray menu.

## Install

Requirements: Windows 10/11, Python 3.10+, a microphone. An NVIDIA GPU is
optional but makes it about 10x faster.

```powershell
git clone https://github.com/downmeansoff/fortunevoice-win
cd fortunevoice-win
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

For GPU decoding, also install the CUDA runtime libraries (~1 GB):

```powershell
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

You do **not** need to copy DLLs into System32 — the app registers the
pip-installed CUDA directories with the Windows loader itself.

For the optional cleanup pass, install [Ollama](https://ollama.com/download)
and pull the model:

```powershell
ollama pull gemma3:4b
```

## Check the machine before trusting it

```powershell
python -m fortunevoice doctor
```

Verifies the hotkey string, enumerates and actually records from the
microphone, loads the model and reports which backend it landed on, decodes a
second of audio, and checks Ollama. Every "it just doesn't type anything"
report comes down to one of those.

## Run

```powershell
python -m fortunevoice
```

A tray icon appears: grey while the model loads, green idle, red recording,
amber transcribing. Hold **Ctrl+Alt+Space**, speak, release.

To start it without a console window, use `pythonw.exe`:

```powershell
.venv\Scripts\pythonw.exe -m fortunevoice
```

Put a shortcut to that in `shell:startup` to have it running at login.

## Settings

`%APPDATA%\FortuneVoice\config.json` — plain JSON, edited by hand, re-read
without a restart. Only values you change are stored.

| Key | Default | What it does |
|---|---|---|
| `FVHotkey` | `ctrl+alt+space` | Hold-to-talk chord. `f9`, `rctrl`, `ctrl+space`… |
| `FVActivationMode` | `hold` | `hold` (push-to-talk) or `toggle` (tap on, tap off) |
| `FVModel` | `large-v3-turbo` | Whisper weights |
| `FVFallbackModel` | `small` | Used when the above won't load |
| `FVDevice` | `auto` | `auto`, `cuda`, `cpu` |
| `FVLanguage` | `ru` | `ru`, `en`, … or `auto` |
| `FVCleanupEnabled` | `true` | LLM cleanup pass |
| `FVSmartFix` | `true` | Repair garbled transcripts even when cleanup is off |
| `FVOllamaModel` | `gemma3:4b` | Cleanup model |
| `FVStreaming` | `true` | Decode while you speak |
| `FVMicrophone` | `""` | Input device name substring; empty = system default |
| `FVPasteViaClipboard` | `false` | Clipboard + Ctrl+V instead of typing (for apps that ignore synthesized input) |
| `FVDebugTimings` | `false` | Log the full latency breakdown |
| `FVRetentionDays` | `0` | Days of history to keep; 0 = forever |

Custom vocabulary — names and jargon Whisper keeps mishearing — goes in
`%APPDATA%\FortuneVoice\dictionary.json` as a JSON list of strings.

## Where things live

Everything is under `%APPDATA%\FortuneVoice`:

```
config.json      settings
dictionary.json  custom vocabulary
history.json     every dictation (the vault — written before anything can fail)
metrics.jsonl    per-dictation timings; `python -m fortunevoice stats` summarises them
recovery\        audio from dictations the decoder failed on, for a manual retry
logs\            rotating log file
models\          downloaded Whisper weights
```

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

90 tests: the macOS project's own suite ported over (sentence splitting,
repeat collapsing, the cleanup skip heuristic and cost model, the streaming
silence rules, the decoder gate's bounded waits, timeouts, history, recovery,
stats) plus Windows-specific ones for hotkey parsing, the hook's swallow/fire
decisions, and the config file.

There is also an opt-in test that drives real Win32 input:

```powershell
pytest -m live
```

It needs a desktop session with nothing stealing focus, and it **skips** —
never types — unless it can prove its own window is in the foreground. Run it
from a normal terminal, not from a remote or automated session.

## Measured on this machine

RTX 3060 Laptop (6 GB), `large-v3-turbo` at `cuda/float16`:

| | |
|---|---|
| 10.6 s of Russian, batch decode | 875 ms (12x realtime) |
| same clip, streaming key-up decode | 438 ms — half the wait |
| 7.0 s of English, batch decode | 375 ms (19x realtime) |
| model load, cached | ~5 s |

Streaming pays off on longer dictations; on short ones it deliberately does
nothing (the first pass is delayed 3 s, because a pass in flight at key-up
would only delay the final decode).

## Known limits

- **No result panel.** When the text can't be typed (you switched windows, no
  editable field), macOS shows a floating panel with a Copy button. Here you
  get a tray balloon and the transcript in History; "Copy last dictation" in
  the tray menu puts it on the clipboard.
- **Editable-field detection is weak.** Windows has no cheap equivalent of the
  macOS Accessibility role query, so the app types unless it is certain there
  is nowhere to type. Same bias as the original: losing a dictation is worse
  than an occasional stray paste.
- **The hotkey is a low-level keyboard hook.** Windows drops such hooks if the
  callback is too slow; this one only enqueues, and reinstalls itself if it
  ever does run long. If dictation stops responding, that is the first thing
  to check in the log.
