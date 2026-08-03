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

## The UI, rebuilt in Tk

The AppKit/SwiftUI surface was dropped in the first pass and rebuilt after: an
app whose only feedback was a tray colour gave no way to tell "listening" from
"crashed". All of it is tkinter, which ships with Python — no extra wheel, and
nothing to install on a machine that is already running the app.

**Two GUI loops in one process.** pystray runs a Win32 message loop and wants
the main thread; Tk wants a `mainloop` of its own. Tk gets a dedicated thread
(`ui/__init__.py`), and every widget in the package lives on it. Callers hand
a callable to `ui.call()`; nothing outside that module touches a Tk object.
Tkinter is not thread-safe and breaking the rule does not raise — it corrupts
the interpreter and crashes minutes later somewhere unrelated.

**The overlay must not take focus.** This is the detail the whole pill depends
on: FortuneVoice types into whatever window has focus, so an overlay that
activated when shown would *become* that window and receive the dictation
itself. `WS_EX_NOACTIVATE` plus `SetWindowPos(SWP_NOACTIVATE)` instead of
Tk's `lift()` (which activates), plus `WS_EX_TOOLWINDOW` to stay out of
Alt-Tab, plus `WS_EX_TRANSPARENT` so it never swallows a click meant for the
app underneath.

**Rounded corners** come from Tk's `-transparentcolor`: the capsule is drawn
on a chroma-key background that Windows turns into a hole. The waveform is a
scrolling history of per-block RMS — the app has no spectrum analyser, and a
scrolling level history is an honest picture of what it does have.

**ttk is avoided throughout.** On Windows the native theme engine overrides
background colours on ttk widgets, so a dark `ttk.Notebook` renders grey and a
`ttk.Button` refuses a dark fill. Every control here is a plain widget or a
Canvas drawing: the nav rail, the iOS-style switches, the value dropdowns
(a Canvas chip that posts a `tk.Menu`, which *does* honour colours), the
chips, and the rounded cards.

**`Card` is the recurring trick.** A Canvas paints the rounded rectangle and
hosts an ordinary Frame inset far enough that the Frame's square corners never
poke through the drawn ones. It auto-sizes from the content's requested
height, because a Canvas otherwise has no idea what is inside it and falls
back to its 7 cm default — which is what a "why is every history card 250 px
tall" bug looks like.

**Glyphs are drawn in code** (`ui/icons.py`). SF Symbols have no Windows
equivalent worth depending on: Segoe Fluent Icons is Windows 11 only and
addresses glyphs through private-use codepoints, and colour emoji clash with a
flat blue accent.

Four bugs from this pass, all found by looking at screenshots rather than by
reading the code:

- `ImageDraw.arc` takes an absolute **end** angle; the Tk canvas takes a
  **sweep**. Writing the Tk form raised `TypeError` inside the first icon, which
  aborted the whole window build and left it blank.
- `bind("<Configure>", …)` **replaces** an existing binding. The history card
  set its own wraplength handler on the same event `Card` uses to auto-size,
  silently disabling it. `add="+"` is load-bearing.
- A `PhotoImage` referenced only from a short-lived helper object is collected
  and becomes a blank square. Tk keeps child *widgets* alive through
  `master.children`, so the reference has to hang off the widget doing the
  drawing, not off the helper.
- Pillow's ICO writer silently discards requested sizes larger than the image
  being saved (see below).

Two layout bugs worth remembering, both found by screenshotting the windows
rather than by reading the code:

- Controls packed hard-right inside a scrollable canvas land *under* the
  scrollbar and are clipped; the rows need explicit right padding.
- `side="bottom"` only claims what is left at the moment of packing, so a
  footer built last is pushed off the window entirely. It has to be packed
  before the content above it.

### Still deliberately dropped

- **The spectrum orb.** The macOS pill ran a real FFT; this one shows an RMS
  waveform. At 188x40 the difference is not visible.
- **Login item.** `scripts/install_shortcut.py --startup` writes a shortcut to
  the Startup folder, which is the same thing without code.
- **Accessibility/microphone permission prompts.** Windows has no equivalent
  gate for either, so there is nothing to request.

### The icon

Generated in code (`assets.py`), not shipped as an opaque binary: one drawing
produces the multi-resolution `.ico` for Explorer and the per-state tray
bitmaps, so the tray and the desktop shortcut are visibly the same app, and
the mark is reviewable in a diff.

Pillow's ICO writer silently discards any requested size larger than the image
being saved. Building the file from the 16 px render therefore produced a
one-frame icon that Explorer upscaled — a blurry desktop icon with no error
anywhere. The base has to be the largest frame; `tests/test_assets.py` pins it.

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

This is a **model** finding, not a port bug — the prompts are byte-identical
to upstream's.

### Head to head against qwen2.5:3b

Same samples, three runs per prompt tier, scored on two axes because they fail
differently. *fixes* = made the edit the prompt asks for; *harms* = damaged the
text (leading bullet/dash, meaning change, already-clean text rewritten).

| Model / tier | fixes | harms | median |
|---|---|---|---|
| gemma3:4b, mini | 6/15 | 0/15 | 641 ms |
| gemma3:4b, full | 9/15 | **9/15** | 703 ms |
| qwen2.5:3b, mini | 9/15 | 0/15 | 297 ms |
| **qwen2.5:3b, full** | **12/15** | 2/15 | **297 ms** |

`qwen2.5:3b` on the full prompt collapses the stumble
(`нужно правильно это правильно писать` → `нужно правильно это писать`, keeping
the ё), applies the self-correction, removes the English filler, and never
emits a stray leading dash. Its two harms are one run in three that
lower-cased the opening word — a capitalisation flake, not a meaning change.
It is also 2.4x faster and 1.9 GB against 3.3 GB, which matters on a 6 GB card
that is already holding Whisper.

**This inverted the mini-prompt trade.** The mini tier exists because
prompt-eval dominates the round-trip on a slow model. On a model that answers
in ~300 ms it only loses accuracy: measured, qwen's mini tier failed the
stumble and self-correction cases its full tier passed, and stripped commas
the full tier kept. Since upstream's routing is hard-coded, this port adds
`FVMiniPrompt` (default `true`, i.e. upstream behaviour) so the tier can be
turned off. `warmup()` skips priming the mini prefix when it is off, rather
than paying a round-trip on hotkey-down for a prompt that will never be used.

### What qwen still gets wrong

Not a recommendation to trust it blindly. Through the real `clean()` path at
the app's 1.5 s budget:

- `ну я это, короче, хотел сказать что завтра встреча` →
  `я хотел сказать, что завтра есть встреча` — **invented «есть»**, which the
  prompt explicitly forbids.
- `сделай кнопку синим, нет, красным цветом` → `сделай красным цветом` — the
  self-correction is right but **«кнопку» was dropped**. Six words to three is
  a 50% loss, and it slipped past the 35% safety net by exactly one word:
  the guard is `clean < int(raw * 0.65)`, and `3 < int(3.9)` is `3 < 3`, false.
  The threshold is upstream's and is left alone, but it is a hair's breadth
  from catching this.

So: qwen2.5:3b is clearly the better of the two, and cleanup is genuinely
useful with it, but it is still an LLM rewriting the user's words. The raw
transcript is always kept in History alongside the cleaned one for exactly
this reason.

### The cost predictor is now too conservative

`predicted_ms = 900 + 6.25·chars` was fitted against live gemma3:4b runs on
Apple silicon. qwen2.5:3b answers in ~280 ms where the model predicts 1200+.
With the 1.5 s budget that means **anything past ~96 characters is declined up
front** — the 124-char sample above spent 0 ms and returned raw, though qwen
would have cleaned it in ~300 ms.

Deliberately not refitted here: upstream's own comment warns that a synthetic
benchmark underestimates the constant, because hammering Ollama keeps the
model and prompt cache maximally hot while a real dictation arrives after a
gap. `metrics.jsonl` records `cleanup_ms` and `chars` on every dictation
precisely so this can be refitted from real use. Do that after a week of
actual dictation, not from a loop like the one above.

## Still unverified

- **Dictation with a real voice through a real microphone.** Everything up to
  and including the decode is exercised above, but nobody has spoken into it.
- **A cleanup model that actually suits these prompts.** Only `gemma3:4b` has
  been measured, and it is the one upstream benchmarked on Apple silicon.
- **The CPU fallback rung** of the backend ladder — CUDA loaded first try, so
  the `cpu/int8` path was never exercised end to end.
- **Long dictations** near the 300 s cap, and the recovery flow after a real
  decode failure.

## Polish pass: what a tray app has to get right

Things that are invisible when they work and confusing when they do not.

**One instance, enforced.** A named mutex (`Local\FortuneVoice.SingleInstance`,
per-user so two Windows accounts each get their own). Two copies is not a
cosmetic problem: each installs its own low-level keyboard hook, so one press
starts two recordings and the transcript is typed twice into the user's
document. A second launch shows a message box and exits — silent would look
exactly like failing to start, and the user double-clicks again.

**DPI awareness, set before any window exists.** Asking afterwards is ignored
and Windows bitmap-stretches the whole UI: blurry text, blurry icons, and a
pill that lands in the wrong place because the coordinates it reports are
virtualised. `theme.px()` scales the layout constants and Tk's own `tk
scaling` handles fonts. **Unverified at anything but 100%** — the only display
here is 100%.

**The overlay follows the user.** Tk's `winfo_screenwidth/height` describe the
*primary* monitor, so on a two-screen desk the pill appeared on the wrong one,
and even on one screen it ignored the taskbar. Both the pill and the result
panel now position against the work area of the monitor holding the foreground
window.

**Esc cancels.** The audio is dropped without a decode — no transcript, no
history entry, nothing typed. Polled from the thread that already runs for the
life of a recording, rather than through a second global hook, because another
hook is another thing that can wedge the input queue. Deliberately the
opposite of the device-interrupted path, which *salvages*: there the user
still wants their words.

**The shortcut is recorded, not typed.** It is the one setting where a typo is
invisible — a bad string parses into nothing, the hook never fires, and the
app looks dead rather than misconfigured. Changing it rebinds the hook
immediately, because a shortcut you cannot try until after a restart is one
you cannot tell is wrong. Modifier state is read from the physical keyboard
when the trigger key arrives, not from Tk's `event.state`, whose Alt bit
differs between Tk builds.

**Lists instead of free text** for the microphone and both models. The
microphone list stores a *name fragment* rather than an index, because indices
are reassigned as devices come and go and a saved index quietly starts
pointing at a different microphone. The Ollama list shows only what is
actually pulled — offering a model that is not installed produces a setting
that looks applied and fails at the first dictation.

### The cost model now learns

`predicted_ms` was the one number in the port fitted on hardware nobody here
owns: gemma3:4b on Apple silicon, 900 + 6.25·chars. Measured against
qwen2.5:3b on this 3060 it was so conservative that **nothing past ~96
characters was ever attempted** — the budget declined work the machine could
do in a third of the time.

It now fits itself from `metrics.jsonl`, which has recorded `cleanup_ms` and
`chars` per dictation since the first commit precisely so this could be done
from real use rather than from a synthetic loop. Guards, because the failure
direction is asymmetric — underestimating starts work that then runs past the
deadline and is thrown away:

- fewer than 12 recorded cleanups: keep the shipped fit,
- a non-positive slope (longer text coming out cheaper) is noise, not a model,
- the intercept is floored at 250 ms, since a run of cache-hot samples can
  regress to nearly zero.

## Measured: the cleanup model costs 10x the decode speed on a 6 GB card

The single biggest finding of the whole port, and it only shows up end to end.

`KEEP_ALIVE = "24h"` is upstream's, and it is right there: Apple silicon has
unified memory and lots of it, so parking the cleanup model costs nothing. On a
6 GB discrete card it is a disaster. Same 12 s clip, same Whisper instance:

| | VRAM used / free | decode |
|---|---|---|
| qwen2.5:3b resident | 5925 / **72 MiB** | 5766 ms — 2.1x realtime |
| qwen2.5:3b unloaded | 3828 / 2169 MiB | **563 ms — 21.2x realtime** |

`large-v3-turbo` at float16 plus its CUDA context is ~3.8 GB; a resident
qwen2.5:3b adds ~2.1 GB. Together that is 5.9 of 6 GB, and CUDA spends the
difference thrashing. Nothing on screen explains it — dictation just feels
slow, forever.

It reproduces inside a single run: with the GPU free, decodes came back at
563 ms and 312 ms; the moment the test called Ollama and the model loaded, the
next three decodes were 2859, 3406 and 3515 ms.

`doctor` now checks GPU headroom and says so. On a card with 8 GB or more it
stays quiet.

### And the cleanup itself is not earning its keep

Measured against Windows SAPI speech (a clean voice — an accuracy ceiling, not
a typical result), Whisper transcribed all five samples correctly. What
qwen2.5:3b then did to them:

| Said | Cleaned |
|---|---|
| `Ну это самое, короче, надо бы проверить…` | `ну ладно, нужно бы проверить…` — **invented «ну ладно»**, swapped «надо»→«нужно» |
| `Сделай кнопку синим, нет, красным цветом.` | `сделай кнопку красным?` — right edit, but **became a question** and lost «цветом» |
| `Нужно правильно это правильно писать…` | `нужно правильно это писать…` — correct |

Plus a consistent lower-casing of the opening word. Two of three edits damaged
the text, on top of the 10x decode tax.

So on this machine cleanup is off (`FVCleanupEnabled`, `FVSmartFix` both
false). Whisper already punctuates and capitalises; the LLM pass was removing
value in both directions. Worth revisiting with a model that fits beside
Whisper — qwen2.5:1.5b — rather than treating this as settled.

## The DPI pass was half-done, and I could not see it

Worth recording as a method failure, not just a bug.

`theme.px()` scaled the structural constants — window size, sidebar width, card
radius, row heights. It did **not** scale what widgets draw inside themselves:
dropdown chip geometry, the app badges, icon sizes, the pill's label column.
Meanwhile `tk scaling` made every font 25% larger. So on the target machine —
a **125%** display — the text grew and the boxes around it did not. Chevrons
sat on top of the last word, icons looked undersized next to their labels, and
the whole thing read as cheap.

The reason it survived review is the more useful lesson: **every screenshot
harness ran in a DPI-unaware process.** Windows virtualises those — they are
told the display is 96 dpi no matter what it is — so every render I checked
was a 100% render. `winapi.scale_factor()` called from such a process returns
1.0 and looks like confirmation. The app itself calls `set_dpi_awareness()`
in `__main__` and saw 1.25.

Two changes came out of it:

- `theme.text_width()` measures strings with `tkinter.font.measure` instead of
  estimating "7 px per character". The estimate was calibrated at 100% and had
  no way to be right anywhere else. Chips, dropdowns and the pill's label
  column are now sized from the measurement.
- Every screenshot harness calls `set_dpi_awareness()` before importing the
  package, so what gets rendered is what the user gets.

### The white title bar

Tk styles the client area only, so a dark app gets the default Windows title
bar on top of it — light, with the app's own dark surface starting one pixel
below. It is the single most "unfinished" thing about the UI and it shows on
every window at once.

There is no Tk option for it. `DwmSetWindowAttribute` with
`DWMWA_USE_IMMERSIVE_DARK_MODE` is the documented fix; the attribute number
changed between Windows 10 builds (19 before 1903, 20 after), so
`winapi.use_dark_titlebar` tries both and stops at the first that returns
success.

## Russian, and the language split that matters

The UI ships in English and Russian (`strings.py`, ~120 keys). One flat
catalogue rather than gettext: two languages and no build step beats a .po
toolchain in a project whose selling point is "clone it and run it".

`FVUILanguage` (`ru` / `en` / `auto`, default `auto` following the Windows
display language) is deliberately **separate** from `FVLanguage`. The language
you dictate in has nothing to do with the language you want the buttons in,
and a Russian speaker dictating English notes should not have their UI flip.

Navigation keys stay English internally (`"History"`, `"Settings"`); only the
drawn label is translated. A language change therefore cannot break which page
a click opens.

Changing it needs a restart, and says so. Half the strings are baked into
module constants at import — the pill sizes itself from its longest label —
so live re-translation would mean rebuilding every window.

## The cleanup model, settled

Three models measured on this 6 GB card, same clip, same Whisper instance:

| Model | VRAM free with Whisper | Decode | Verdict |
|---|---|---|---|
| gemma3:4b | — | — | leaks stray `- `/`— ` into prose, rewrote a statement as a question |
| qwen2.5:3b | **72 MiB** | 5766 ms (2.1x) | best edits, but **10x the decode cost** |
| **qwen2.5:1.5b** | 420 MiB | **406 ms (29.5x)** | fits for free; edits are decent but it invented a sentence |

1.5b costs *nothing*: 406 ms with it resident, 406 ms without. So the choice
stopped being about speed and became about trust.

### The guard that made it usable

The shipped 35% word-drop net only catches **deletion**. On the filler sample
1.5b returned *"Ну это самое, короче, нужно правильно это писать"* — same
length, different sentence, straight past the guard, and the app would have
typed it. Substitution is the worse failure and nothing caught it.

`_no_invented_content` compares 4-character stems of the output against the
input: more than 30% unfamiliar and the raw text wins. Stems, not whole words,
so Russian inflection and the ё Whisper drops don't read as invention. Verified
against every measured case — the bad rewrite is rejected, and collapsing a
stumble, applying a self-correction, dropping a filler and re-casing all pass.

It fires in production: the log line is `cleanup invented content, using raw
text`.

Cleanup is therefore **on**, with qwen2.5:1.5b. Worst case is now the raw
transcript, which is what the app would have typed anyway.

## The cold start that made cleanup miss its budget

Measured on a real dictation: one cleanup took **2016 ms against a 1500 ms
budget**. Not a slow model — a cold one.

`warmup()` runs on hotkey-down, while the user is still speaking, so the real
call pays only for the user's text. It was throttled to once per 10 minutes
after a success, on the reasoning that `keep_alive = "24h"` keeps the model
loaded anyway.

That assumption breaks in the two situations that matter most here:

* Ollama restarts (it has no autostart on Windows, so this is routine), and
* Ollama **evicts** the model under VRAM pressure — exactly what happens on a
  6 GB card when Whisper loads beside it.

In both, `warmup()` returned early on a model that was no longer there, and
the next dictation paid the cold load inside its own budget.

The fix is to stop trusting elapsed time and check the fact: `/api/ps` lists
what Ollama actually has in memory. Warm-up now skips only when a recent prime
succeeded **and** the model is still resident. The probe has a 1 s timeout and
"don't know" means "prime anyway" — it sits on hotkey-down and its answer is
only ever used to skip work.

Proven on the exact failure:

| | |
|---|---|
| after priming | resident |
| after eviction | not resident |
| **old code would skip warm-up** | **yes** |
| new code re-primes | 3.6 s, while the user is still talking |
| first cleanup after eviction | **359 ms** (was 2016 ms) |

## "Продолжение следует" — the guard that never fired

Reported from real use: hold the hotkey, say nothing, and the app types
**«Продолжение следует.»** into the document. Whisper is trained on subtitle
corpora and fills an empty room with their boilerplate.

There was already a guard for exactly this — and it had never once fired.
Recording real silence from this machine's microphone, four runs out of four:

| rms | no_speech_prob | heard |
|---|---|---|
| 0.00076 | **0.000** | Продолжение следует. |
| 0.00146 | **0.000** | Продолжение следует. |
| 0.00029 | **0.000** | Продолжение следует. |
| 0.00080 | **0.000** | Продолжение следует. |

The guard required `no_speech_prob > 0.6 **AND** rms < 0.006`. The audio half
was right — RMS correctly read a silent room, four times under the threshold.
The model half was not merely wrong but maximally wrong: **0.000**, total
confidence that the room noise was speech. With an `AND`, one broken signal
disabled the whole guard.

`no_speech_prob` is now not consulted at all. `textclean.is_hallucinated_silence`
decides on two independent signals, either sufficient:

1. **RMS below the silence floor** — the loudest 0.5 s window in the whole
   recording never reached speech level. Measured room noise is 0.0003–0.0015
   and speech is 0.02+, so 0.006 sits with a 4x margin over one and a 3x
   margin under the other.
2. **Subtitle boilerplate in the band just above it** — a fan or street noise
   can lift RMS over the floor while the room still holds no speech. The
   phrase list covers the strings Whisper actually emits in Russian and
   English.

The phrase net stops at 4x the floor, so a person who genuinely says
"продолжение следует" out loud is never censored: at speech volume only the
audio decides. Verified — the same sentence passes at rms 0.15.

A caught hallucination is still saved to History, untyped. A false positive
must always be recoverable; a truly silent drop never happens.

## Recording a shortcut fought the app's own hook

Reported: the Shortcut setting could not be changed.

The widget itself was fine — driven directly it captured keys, ignored bare
modifiers and saved correctly. Three things on the real path were not.

**The hook swallowed the chord.** The global `WH_KEYBOARD_LL` hook claims the
configured hotkey and returns 1, so the key never reaches Tk. The chord a user
reaches for first when changing a shortcut is *the one already set* — pressing
it started a dictation instead of recording anything. `App.pause_hotkey()` now
uninstalls the hook while the recorder is listening and `resume_hotkey()`
puts it back, picking up whatever was just saved.

**The target was 150 px.** For the setting people most want to change, a click
a pixel outside the chip did nothing at all. The whole row is clickable now.

**Keys followed focus.** The binding lived on the chip, so any click that
moved focus elsewhere in the page left the chip *looking* like it was
listening while the keypress went somewhere that ignored it. It is now bound
on the toplevel.

Deliberately not `bind_all`: that registers for the whole interpreter, and the
matching `unbind_all("<KeyPress>")` wipes **every** other key handler in the
app rather than just this one. The funcid returned by `bind` is kept so
exactly one handler is removed. The test suite found this immediately — two
recorders in one interpreter stopped seeing each other's keys.
