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
                      → [optional] Ollama qwen2.5:1.5b cleanup → typed into the focused app
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
ollama pull qwen2.5:1.5b
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
python scripts\install_shortcut.py
```

Puts a FortuneVoice shortcut on your Desktop pointing at `pythonw.exe` (no
console window). Add `--startup` to also launch it at login, `--remove` to
take both away. Or run it directly:

```powershell
.venv\Scripts\pythonw.exe -m fortunevoice
```

## While you dictate

Press **Esc** to throw a recording away — no decode, no transcript, nothing
typed. A misfired hotkey otherwise costs a full decode and whatever the model
invents out of room noise.

Only one copy runs at a time. A second launch says so and exits, because two
instances would each install a keyboard hook and type every dictation twice.

## What you see

A tray icon — the app mark, tinted: grey while the model loads, pale idle, red
recording, amber transcribing. Its level bars grow while audio is arriving, so
a dead microphone doesn't look like a working one.

Hold **Ctrl+Alt+Space**, speak, release. While you hold it, a small pill floats
near the bottom of the screen with a live waveform, so you can see it is
listening without looking away from what you're writing. It never takes focus
and never eats a click.

If the text can't be typed — you switched windows, there's no editable field —
a panel appears with the transcript and a Copy button. Nothing reaches your
clipboard until you press it.

The tray menu opens the main window: **History** (searchable; click a card to
copy it, ✕ to delete it), **Insights** (words per minute, day streak, where
the words went, measured latency), **Dictionary** (names Whisper keeps
mishearing) and **Settings**. A first-run screen shows the hotkey and proves
the microphone with a live meter; reopen it any time from the tray.

Settings is the same file as `config.json`, with the fiddly parts made
safe: the shortcut is **recorded** rather than typed (click it, press the
keys) and applies immediately without a restart, the microphone and both
models are picked from lists of what is actually installed, and *Launch at
login* writes a real Startup shortcut.

Set `"FVOverlay": false` if you'd rather not have the pill.

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
| `FVLanguage` | `ru` | Language you **speak**: `ru`, `en`, … or `auto` |
| `FVUILanguage` | `auto` | Language of the app's **windows**: `ru`, `en`, `auto` (follow Windows) |
| `FVCleanupEnabled` | `true` | LLM cleanup pass |
| `FVSmartFix` | `true` | Repair garbled transcripts even when cleanup is off |
| `FVOllamaModel` | `gemma3:4b` | Cleanup model — set `qwen2.5:1.5b`, see below |
| `FVMiniPrompt` | `true` | Short prompt for short dictations; turn off on a fast model |
| `FVStreaming` | `true` | Decode while you speak |
| `FVMicrophone` | `""` | Input device name substring; empty = system default |
| `FVPasteViaClipboard` | `false` | Clipboard + Ctrl+V instead of typing (for apps that ignore synthesized input) |
| `FVDebugTimings` | `false` | Log the full latency breakdown |
| `FVRetentionDays` | `0` | Days of history to keep; 0 = forever |
| `FVOverlay` | `true` | The floating pill while you speak |
| `FVSounds` | `true` | Start/success/error/cancel tones |
| `FVWindowGeometry` | `""` | Remembered main-window size and position |
| `FVOnboarded` | `false` | Set once the first-run screen is dismissed |

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

## Licensing — read before sharing this

**This repository has no licence yet, and cannot get one unilaterally.**

It is a derivative work: the prompts, the tuning constants, the streaming
algorithm and most of the test suite come from
[thatluckyoldsun/FortuneVoice](https://github.com/thatluckyoldsun/FortuneVoice),
which itself carries **no licence file**. Under copyright law that means all
rights reserved — the default when nothing is stated — so nobody, including
contributors, may redistribute it or grant terms on it.

To make this shareable, the upstream author has to state a licence for their
repository first. Once they do, the same one (or a compatible one) goes here
and this section is replaced by a real `LICENSE`.

Until then: fine to run on your own machine, not fine to publish or hand out.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

116 tests: the macOS project's own suite ported over (sentence splitting,
repeat collapsing, the cleanup skip heuristic and cost model, the streaming
silence rules, the decoder gate's bounded waits, timeouts, history, recovery,
stats) plus Windows-specific ones for hotkey parsing, the hook's swallow/fire
decisions, the config file, the generated icon, the prompt-tier routing, the
learned cleanup cost model and the remembered window geometry.

Two more opt-in suites, both needing a desktop session:

```powershell
pytest -m ui      # builds every window for real
```

`-m ui` exists because a window that raises inside a UI callback is
swallowed by the event pump and simply never appears — which looks exactly
like the app not starting.

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

## About the AI cleanup

**Use `qwen2.5:3b`, not the default `gemma3:4b`.** Measured on Russian
(three runs per case, [PORT_NOTES.md](PORT_NOTES.md) has the table):
gemma3:4b on the full prompt damaged the text in 9 of 15 runs — a stray
leading `- `/`— ` on ordinary prose, and an already-clean statement rewritten
as a question. qwen2.5:3b made the intended edit in 12 of 15, harmed 2, and
ran 2.4x faster at 1.9 GB instead of 3.3 GB.

```powershell
ollama pull qwen2.5:3b
```

```json
{ "FVOllamaModel": "qwen2.5:3b", "FVMiniPrompt": false }
```

`FVMiniPrompt: false` matters here. Short dictations normally take a
stripped-down prompt to save prompt-eval time; on a model that answers in
~300 ms that trade is pure loss, and qwen's mini tier failed cases its full
tier passed.

Even then it is an LLM rewriting your words — in testing it once invented a
word and once dropped one. The raw transcript is always kept in History next
to the cleaned one. If you would rather not have it edit at all:

```json
{ "FVCleanupEnabled": false }
```

`large-v3-turbo` already punctuates and capitalises well enough that the
end-to-end transcripts above needed no cleanup. `FVSmartFix` stays useful
either way — it only engages on low-confidence decodes, where the transcript
is garbled enough that a rewrite can only help.

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
