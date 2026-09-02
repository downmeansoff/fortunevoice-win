# FortuneVoice for Windows

[![CI](https://github.com/downmeansoff/fortunevoice-win/actions/workflows/ci.yml/badge.svg)](https://github.com/downmeansoff/fortunevoice-win/actions/workflows/ci.yml)

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
                      → [optional] Ollama qwen2.5:3b cleanup → typed into the focused app
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
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

That one script does the lot: creates the venv, installs the dependencies,
adds the CUDA wheels **only if** there is an NVIDIA GPU to use them, pulls the
cleanup model when Ollama is installed, puts a shortcut on the Desktop, and
finishes by running `doctor` — so a machine that cannot run this says so
during setup instead of at the first dictation.

Prefer to do it by hand:

```powershell
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
ollama pull qwen2.5:3b
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
invents out of room noise. You get the cancel tone and a "Cancelled" pill, so
you know it took.

Say **"new line"** — or "новая строка" — as a sentence of its own and you get
a line break; "new paragraph" gives two. That exists because Enter *sends* the
message in most chat applications, so a dictation that wanted two lines could
not be fixed up afterwards without fighting the app. The phrase only counts
when it stands alone: "I started on a new line, because it reads better"
survives untouched, or there would be no way to say the words at all. Turn it
off in Settings if you'd rather have the words.

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

Settings takes any chord, including a bare modifier — `Ctrl`, `Alt`, `Ctrl+Alt`.
Those keep working as themselves: they are never swallowed, and a short tap is
ignored, so Ctrl+C stays Ctrl+C. Hold one for about a third of a second and
dictation starts.

If the text can't be typed — you switched windows, there's no editable field —
a panel appears with the transcript and a Copy button. Nothing reaches your
clipboard until you press it.

When it *was* typed but landed in the wrong window, the tray has **Type last
dictation here**: click into the right window, pick it from the menu, and the
last transcript is typed there. Worth knowing about, because most deliveries
go out "blind" — Windows often will not confirm there is an editable field,
and the app types anyway rather than refusing. It is in the menu rather than
on a shortcut of its own: with `Ctrl+Alt` as the dictation chord, every
`Ctrl+Alt+X` a second shortcut could use would also start a dictation.

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
| `FVHotkey` | `ctrl+alt+space` | Hold-to-talk chord. `f9`, `rctrl`, `ctrl+space`, `ctrl+alt`… |
| `FVActivationMode` | `hold` | `hold` (push-to-talk) or `toggle` (tap on, tap off) |
| `FVModel` | `large-v3-turbo` | Whisper weights |
| `FVFallbackModel` | `small` | Used when the above won't load |
| `FVDevice` | `auto` | `auto`, `cuda`, `cpu` |
| `FVLanguage` | `ru` | Language you **speak**: `ru`, `en`, … or `auto` |
| `FVUILanguage` | `auto` | Language of the app's **windows**: `ru`, `en`, `auto` (follow Windows) |
| `FVCleanupEnabled` | `true` | LLM cleanup pass |
| `FVSmartFix` | `true` | Repair garbled transcripts even when cleanup is off |
| `FVVoiceCommands` | `true` | «новая строка» / «новый абзац» become line breaks |
| `FVOllamaKeepAlive` | `5m` | How long Ollama holds the model. `24h` keeps ~2 GB of video memory busy |
| `FVAutoStartOllama` | `true` | Start Ollama when cleanup needs it and nothing is listening |
| `FVAppProfiles` | `{}` | Per-application overrides — see below |
| `FVOllamaModel` | `qwen2.5:3b` | Cleanup model. Smaller ones translate the text instead of cleaning it |
| `FVCleanupDevice` | `gpu` | Where the cleanup model runs. On a 6 GB card with Whisper resident, `qwen2.5:3b` will not load on the GPU at all (`cudaMalloc failed: out of memory`) and `qwen2.5:1.5b`, which does fit, had every one of four cleanups rejected by the safety guards. On `cpu` the same 3b takes ~4 s warm, uses no video memory, and works — seconds instead of gigabytes. |
| `FVOllamaHost` | `http://localhost:11434` | Where Ollama is. Point it at another machine to keep the video memory here free |
| `FVMiniPrompt` | `true` | Short prompt for short dictations; turn off on a fast model |
| `FVUnloadModelAfter` | `0` | Minutes of no dictation before Whisper is dropped from video memory. `0` = never. It holds ~2.1 GB while idle; reloading costs 5.6 s on the next dictation |
| `FVStreaming` | `true` | Decode while you speak |
| `FVStreamingV2` | `true` | Type the stitched streaming result rather than re-decoding the whole recording |
| `FVStreamingShadow` | `false` | Compute both and log the difference. A second full decode per dictation — for checking a change, not for daily use |
| `FVMicrophone` | `""` | Input device name substring; empty = system default |
| `FVDelivery` | `auto` | How the text gets in: `auto` types short dictations and pastes long ones, `type` always types, `paste` always pastes. Typing costs one keystroke per character and the receiving app pays it — a long dictation into an editor takes tens of seconds — while a paste is one event whatever the length. |
| `FVPasteOver` | `120` | Characters above which `auto` pastes. |
| `FVPasteViaClipboard` | `false` | Always paste. Predates `FVDelivery`; still wins when true. |
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

534 tests, run on every push by CI: the macOS project's own suite ported over (sentence splitting,
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

`qwen2.5:3b` is the default, and the smallest model that does the job.
Measured on Russian (four samples each, with `large-v3-turbo` already resident
on a 6 GB card):

| model | time | result |
|---|---|---|
| `qwen2.5:1.5b` | ~330 ms | **translated all four** — three into English, one into Chinese |
| `qwen2.5:3b` | 546-657 ms | cleaned all four, kept the language |
| `gemma3:4b` | 1141 ms | cleanest text, but prefixed a bullet |

Every 1.5b answer was rejected by the invented-content check, so cleanup
silently did nothing and the raw transcript was typed instead. Small is not
free.

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

- **A paste can be silently ignored.** Some terminals take Ctrl+Shift+V
  rather than Ctrl+V, and Tk applications bind paste to the letter "v",
  which a Russian layout does not produce. The transcript is in History
  either way, but if a particular app swallows pastes, give it
  `"FVDelivery": "type"` in `FVAppProfiles`.
- **Editable-field detection is weak.** Windows has no cheap equivalent of the
  macOS Accessibility role query, so the app types unless it is certain there
  is nowhere to type. Same bias as the original: losing a dictation is worse
  than an occasional stray paste.
- **The hotkey is a low-level keyboard hook.** Windows drops such hooks if the
  callback is too slow; this one only enqueues, and reinstalls itself if it
  ever does run long. If dictation stops responding, that is the first thing
  to check in the log.

### Per-application rules

Dictating a shell command and dictating a chat message want opposite things.
Cleanup fixing punctuation is right for the message and wrong for
`git rebase -i HEAD~3`, which it will happily capitalise into something that
does not run.

Add overrides to `config.json` (the tray opens the folder), keyed by
executable name:

```json
"FVAppProfiles": {
  "WindowsTerminal.exe": { "FVCleanupEnabled": false },
  "Telegram.exe": { "FVVoiceCommands": true }
}
```

Only per-dictation behaviour can be overridden — `FVCleanupEnabled`,
`FVSmartFix`, `FVVoiceCommands`, `FVStreaming`, `FVPasteViaClipboard`,
`FVDelivery`,
`FVMiniPrompt`. Not hosts, paths or the hotkey: those are properties of the
installation, not of whichever window happens to be in front.

### Correcting a dictation

Double-click a transcript in History to fix it. Enter saves, Escape abandons,
Shift+Enter adds a line break. The original speech is kept, so a bad edit is
always recoverable.

A correction is also the one moment the app knows for certain that a word was
misheard **and** what the right word was, because you just typed it — so names
and jargon you introduce are added to the dictionary and bias the next decode.
Ordinary word swaps are not: only words of four characters or more, in Latin
script or capitalised mid-sentence, and never more than three from one edit.
