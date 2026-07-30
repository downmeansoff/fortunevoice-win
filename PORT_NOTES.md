# Port notes: macOS FortuneVoice → Windows

Upstream: [thatluckyoldsun/FortuneVoice](https://github.com/thatluckyoldsun/FortuneVoice),
at merge commit `04e2e26` (PR #3).

## Why this is a rewrite and not a build fix

Nothing in the macOS app runs on Windows:

| Upstream dependency | Why it can't cross |
|---|---|
| WhisperKit | CoreML + Apple Neural Engine, Apple silicon only |
| AppKit / SwiftUI | macOS only; no Windows backend exists |
| `CGEvent` + Accessibility | macOS input synthesis and permission model |
| `AVAudioEngine` | Apple audio stack |
| `KeyboardShortcuts` | macOS global shortcut registration |

The Swift toolchain does exist on Windows, but without AppKit or CoreML it
would buy nothing: roughly 85% of the 4 771 lines are platform surface. What
is genuinely portable is the *design* — and it is the design, not the Swift,
that took three iterations to get right upstream.

## Module map

| macOS (Swift) | Windows (Python) | Port kind |
|---|---|---|
| `TextSegmenter.swift` | `segmenter.py` | line-for-line |
| `Transcriber.collapseRepeats` | `textclean.py` | line-for-line |
| `StreamingSession.wordDiff` | `textclean.word_diff` | line-for-line |
| `OllamaCleaner.swift` | `cleaner.py` | prompts and thresholds verbatim; `URLSession` → `urllib` |
| `StreamingSession.swift` | `streaming.py` | algorithm identical; Swift actors → threads |
| `Timeout.swift` | `timeout.py` | same abandon-don't-kill semantics |
| `SerialGate` (in `Transcriber.swift`) | `transcriber.SerialGate` | same bounded-wait FIFO, `Condition` instead of continuations |
| `Transcriber.swift` | `transcriber.py` | **rebuilt** — WhisperKit → faster-whisper |
| `AudioRecorder.swift` | `audio.py` | **rebuilt** — AVAudioEngine → PortAudio |
| `TextInjector.swift` | `injector.py` | **rebuilt** — CGEvent → SendInput |
| `AppDelegate.swift` (hotkey half) | `hotkey.py` | **rebuilt** — `KeyboardShortcuts` → `WH_KEYBOARD_LL` |
| `AppDelegate.swift` (state machine) | `app.py` | structure and every constant preserved |
| `AppDelegate.swift` (menubar) | `tray.py` | **rebuilt** — `NSStatusItem` → pystray |
| `DictationStore` / `RecoveryStore` | `store.py` | same formats, same vault-first ordering |
| `DictationStats.swift` | `stats.py` | line-for-line |
| `Metrics.swift` | `metrics.py` | same field names, so both ports' numbers compare |
| `MainWindow` / `ResultPanel` / `Onboarding` / `Glass` / `RecordingPill` / `SpectrumAnalyzer` | — | **not ported**; see "Deliberately dropped" |
| — | `doctor.py` | **new**; Windows needs it, see below |
| — | `config.py` | **new**; replaces `defaults write` |
| — | `winapi.py` | **new**; shared ctypes declarations |

## Behaviour carried over unchanged

Every constant below is upstream's, and each one exists because of a specific
bug. They are repeated here because they look arbitrary in isolation:

- 200 ms tail capture past key-up (users release on the final syllable).
- 20 s stale-paste limit; the focus recheck after a 500 ms settle.
- 1.5 s cleanup budget, 3.0 s for low-confidence, 0.5 s slack; the
  `900 + 6.25·chars` cost model that declines work up front.
- 25-word selective-cleanup threshold; the 12-word minimum streaming block;
  0.6 s cleanup settle at key-up.
- LocalAgreement-2 plus the quiet-cut rule; the 1.5 s stable margin; the
  adaptive noise floor `min(0.015, max(0.008, floor·3))`.
- The 35% word-drop safety net on every cleanup path.
- The silence-hallucination guard (`no_speech > 0.6` **and** RMS < 0.006), and
  its "still save to History, never a silent drop" rule.
- Retry once on an empty transcript over loud audio, then save to recovery.
- Vault-first delivery ordering, and the outcome sub-reasons in metrics.
- 4 s silence auto-stop in toggle mode; the 300 s hard cap in both modes.
- The decoder gate's bounded waits (180 s real, 5 s warmup).

## What Windows forced

**Hotkey.** `RegisterHotKey` gives no key-up, so hold-to-talk needs a
`WH_KEYBOARD_LL` hook. Two hazards the macOS version never had: Windows
silently unhooks a callback slower than ~300 ms (so the callback only
enqueues, times itself, and reinstalls the hook if it ever runs long), and our
own synthesized keystrokes come back through the hook (so `LLKHF_INJECTED`
events are passed straight through).

**Default chord.** Upstream's ⌥Space is Alt+Space on Windows — the system
window menu. Swallowing it globally breaks a real OS shortcut, and a lone
Alt-release activates the menu bar in Win32 apps. Default is `Ctrl+Alt+Space`;
`FVHotkey` accepts anything else, including single keys like `f9` or `rctrl`.

**Held modifiers.** macOS cleared the flags on its synthetic events. Windows
keeps global modifier state, and users release the trigger key before the
modifiers — so every typed character would arrive as a shortcut. `injector.py`
synthesizes key-ups for whatever is still held before typing.

**Newlines and surrogate pairs.** `KEYEVENTF_UNICODE` with U+000A does nothing
in most apps, and the cleanup model emits `- ` bullets on separate lines, so
newlines go as a real `VK_RETURN`. Anything above the BMP must be sent as two
consecutive code-unit events.

**CUDA DLL discovery.** The single most common "CUDA doesn't work" on an
otherwise healthy Windows box: `pip install nvidia-cudnn-cu12` puts the DLLs
under `site-packages/nvidia/*/bin`, and since Python 3.8 the loader ignores
`PATH` for extension modules, so CTranslate2 reports
`Library cublas64_12.dll is not found`. `transcriber._add_cuda_dll_directories`
registers those directories with `os.add_dll_directory` before the model
loads, instead of asking the user to copy DLLs into System32.

**Backend ladder.** No ANE to fall back from. Instead: CUDA float16 → CUDA
int8_float16 (half the VRAM, for a 6 GB laptop card) → CPU int8. Upstream's
"fall back to `small`" model ladder sits on top of it.

**Decoder options.** faster-whisper is not WhisperKit, so a few settings had
to be chosen rather than copied. `beam_size=1` matches upstream's temperature-0
greedy decode; the temperature fallback list and the tightened
`compression_ratio_threshold=2.0` are upstream's; `condition_on_previous_text`
is **off**, because it is the biggest amplifier of repetition loops in
faster-whisper and dictation windows are short enough not to need it.
`vad_filter` is off so the streaming session's own RMS silence logic stays
authoritative — a second VAD would move the timestamps it cuts on.

**Audio.** We ask the device for 16 kHz and let the Windows audio engine
resample, which works on essentially every modern machine. When a device
refuses, `audio._downsample` decimates from the native rate with a box filter
— crude, but the anti-aliasing is what matters and Whisper's front end
discards everything above 8 kHz anyway.

**Threading.** Swift's `@MainActor` + `async` becomes: the hook thread
enqueues, a controller thread drives the state machine, and each dictation's
pipeline gets its own worker thread. A press arriving mid-transcription is
rejected by the state guard exactly as before; events older than a second are
dropped so a queued press can't start a recording the user abandoned.

## Deliberately dropped

- **Main window, History UI, onboarding, the recording pill, the spectrum
  orb.** All AppKit/SwiftUI. History is still written; it is JSON you can
  open. The pill's purpose — knowing the app is listening — is served by the
  tray icon colour and the start/stop tones.
- **The result panel.** Replaced by a tray balloon plus "Copy last dictation".
  The clipboard stays opt-in, which was the point of the panel upstream.
- **Login item.** A shortcut in `shell:startup` does the same thing without
  code.
- **Accessibility/microphone permission prompts.** Windows has no equivalent
  gate for either, so there is nothing to request.

## Added

**`python -m fortunevoice doctor`.** macOS surfaced failures through system
permission dialogs. Windows fails silently — no mic, a CUDA runtime that won't
load, a model that never downloaded, Ollama not running, a typo in `FVHotkey`.
The doctor checks all five, including an actual one-second recording, because
device enumeration succeeding says nothing about a device another app holds
exclusively.

**`config.json`.** `defaults write` has no Windows equivalent worth emulating.
Key names are kept identical to the macOS `FV*` defaults so both ports' docs
and bug reports stay comparable.

## Verified on hardware

Measured on an RTX 3060 Laptop (6 GB), Windows 11, Python 3.11,
`large-v3-turbo` at `cuda/float16`:

| Check | Result |
|---|---|
| Model load (cached) | 4.5–5.6 s |
| Batch decode, 10.6 s of Russian | 875 ms — 12x realtime, avg logprob −0.11 |
| Batch decode, 7.0 s of English | 375 ms — 19x realtime |
| Streaming key-up decode, same 10.6 s clip | 438 ms after 6 passes — **half** the batch latency, byte-identical text |
| Streaming key-up decode, 7.0 s clip | 391 ms after 3 passes — no gain over batch, which is why the first pass is delayed 3 s |
| Microphone capture | 19 devices enumerated, 1 s captured at 16 kHz |
| SendInput | ASCII, Cyrillic, mixed, punctuation, an emoji surrogate pair, an embedded newline, and a 200-char burst — all round-tripped exactly |
| Offline test suite | 90 passed |

Speech for the decode checks came from Windows SAPI (Irina ru-RU, Zira
en-US), so it is real audio through the real pipeline rather than a mocked
decoder. Both transcripts came back verbatim.

### The foreground trap the live test walked into

`tests/test_injection_live.py` originally trusted Tk's `focus_force()` to put
its own window in front. Windows refuses `SetForegroundWindow` from a process
that is not already foreground and reports no error, so on some runs the
window was merely topmost and the test typed its Cyrillic samples into the
terminal that had launched pytest. The test now proves it owns the foreground
window (comparing the foreground window's PID to its own) and **skips** rather
than typing when it does not.

The app itself is not exposed to this: it types only into the window that was
already in front when the user pressed the hotkey, and `app._deliver` rechecks
that the same window still holds focus — with a 500 ms settle for transient
popups — before typing anything.

## Measured: the cleanup pass, and why it is worth distrusting here

Ollama 0.32.5 with `gemma3:4b` (Q4_K_M) on the same box. The **mechanism** is
healthy — the transport, the budget, the safety nets all behave:

| | |
|---|---|
| Round-trip, 40–124 chars | 578–1094 ms, i.e. **under** the ported `900 + 6.25·chars` prediction in 5 of 5 runs |
| 124-char sample against the real 1.5 s budget | declined up front (`over_budget=1`, 0 ms spent) — exactly the waste the predictor exists to prevent |
| 35% word-drop safety net | fired twice on a 6→2-word rewrite and kept the raw text |
| Cold model load during `warmup()` | **over 120 s**. `keep_alive` is 24h so it happens once, but the first dictation after a reboot gets raw text — which is the designed degradation, not a bug |

The **output quality** is a different story. Three runs per prompt on Russian
samples:

| Sample | mini prompt (< 25 words) | full prompt |
|---|---|---|
| `ну я это, короче, хотел сказать…` | 3/3 edited, removed only «короче» | 3/3 edited, but emitted a leading `- ` bullet |
| `нужно правильно это правильно писать…` | **0/3** — stumble not collapsed | 3/3 collapsed correctly, but emitted a leading `— ` |
| `сделай кнопку синим, нет, красным` | **0/3** — self-correction not applied | 1/3 |
| `Завтра встреча в десять утра.` (already clean) | 0/3 — correctly untouched | **3/3 changed it into a question** |

So on this model: the mini prompt is *safe but weak* (it no-ops rather than
mangles), and the full prompt is *actively unsafe* — the list rule leaks a
stray `- `/`— ` onto the front of ordinary prose, and the punctuation rule
turned a statement into a question. Turning clean text into a question is
worse than doing nothing at all.

Because dictations under 25 words take the mini path, the shipped defaults sit
in the safe regime. Longer ones do not.

This is a **model** finding, not a port bug — the prompts are byte-identical to
upstream's. `large-v3-turbo` already punctuates and capitalises well enough
that the e2e transcripts above needed no cleanup at all, so `FVCleanupEnabled:
false` is a defensible default on Russian here. Worth trying a different
cleanup model before concluding anything about the feature itself.

## Still unverified

- **Dictation with a real voice through a real microphone.** Everything up to
  and including the decode is exercised above, but nobody has spoken into it.
- **A cleanup model that actually suits these prompts.** Only `gemma3:4b` has
  been measured, and it is the one upstream benchmarked on Apple silicon.
- **The CPU fallback rung** of the backend ladder — CUDA loaded first try, so
  the `cpu/int8` path was never exercised end to end.
- **Long dictations** near the 300 s cap, and the recovery flow after a real
  decode failure.
