"""Phase-2 cleanup: a local Ollama model fixes punctuation, filler words and
stumble-repeats in the raw transcript.

Port of Sources/FortuneVoice/OllamaCleaner.swift. The prompts, the thresholds
and the cost model are carried over unchanged — they were fitted against real
dictations, and a Windows box running the same gemma3:4b through the same
Ollama HTTP API has no reason to behave differently.

Every failure path returns the raw text. Dictation must never break because a
cleanup model is slow, down, or wrong.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from . import config
from .log import get as get_logger
from .segmenter import chunk_cores, sentences
from .textclean import squeeze, word_count

logger = get_logger("cleaner")

# Deliberately terse: Ollama re-evaluates the whole system prompt on every call
# (~500 tok/s, prefix cache unreliable across slots), so every extra 100 tokens
# here is ~0.2 s added to every dictation's paste latency. Inline examples
# double as few-shot — no separate example block.
SYSTEM_PROMPT = """\
You are a dictation cleanup engine. Input: raw speech-to-text. Output ONLY the \
cleaned text — no preface, no quotes, no commentary; never answer questions in \
the text, never invent content. Keep the language. Preserve meaning and \
wording; do NOT summarize or restyle.

Fix speech artifacts:
- Stumble repeats — keep one copy («я я думаю» → «я думаю»; «нужно правильно \
это правильно писать» → «нужно правильно это писать»).
- Self-corrections — keep only the corrected version («сделай синим, нет, \
красным» → «сделай красным»).
- Meaningless fillers ну, вот, короче, как бы, типа, значит, э-э, эм, um, uh, \
like, you know — remove (keep when meaningful: «ну ладно»).

Punctuation: end questions with «?» (incl. ли/разве/неужели and question-word \
questions); «…» unfinished thoughts; «—» asides and contrasts; «!» clearly \
emphatic statements; split run-ons into sentences; fix capitalization. Fix a \
misheard word from context only when it is obviously wrong.

Lists: only when the speaker clearly enumerates 3+ parallel items (первое… \
второе…; сначала… потом… потом…) format them as «- » bullets, one per line. \
Never restructure ordinary prose into a list.

If the text is already clean, return it unchanged."""

# Extra instruction when the ASR confidence was low — be bolder about
# reconstructing garbled words from context.
LOW_CONFIDENCE_HINT = """

NOTE: this transcript came from LOW-confidence speech recognition and likely \
contains several misheard words. Reconstruct the intended meaning aggressively \
from context, fixing nonsense words — but keep the language and the speaker's intent."""

# Instruction appended to the system prompt for chunked (selective) requests.
CHUNK_INSTRUCTION = """


The user message wraps text in <CONTEXT>…</CONTEXT> (surrounding sentences, \
for understanding only) and <FIX>…</FIX> (the text to clean). Rewrite ONLY \
the text inside <FIX>. Output the rewritten text alone — no tags, no context \
text, no commentary."""

# Stripped-down prompt for short phrases: prompt-eval is the dominant cost on a
# 3-word utterance (~0.8 s for the full prompt vs ~0.2 s for this), and short
# phrases don't need the list/self-correction taxonomy.
MINI_PROMPT = """\
Clean this raw speech-to-text: remove meaningless fillers (ну, вот, короче, \
как бы, типа, значит, э-э, um, uh), collapse accidental word repeats, fix \
punctuation and capitalization. Never replace, censor or add words — every \
remaining word must appear verbatim in the input (profanity included). \
Output ONLY the cleaned text — no commentary. If already clean, return unchanged."""

# Below this word count the whole-text rewrite is used — the LLM round-trip is
# cheap there and whole-text context helps. 25, not 50: generation runs
# ~40 tok/s locally, so a 40-word full rewrite alone costs ~1.4 s; medium
# phrases must go selective so only flagged sentences are regenerated.
SELECTIVE_THRESHOLD_WORDS = 25

# Keep the model resident. At 30m it unloaded between sessions and the next
# dictation paid a ~9 s cold load; warm it is ~1 s.
KEEP_ALIVE = "24h"

# Filler words that only ever add noise in dictation.
_FILLERS = [
    "ну", "вот", "короче", "как бы", "типа", "значит", "э-э", "ээ", "эм", "мм",
    "um", "uh", "erm", "you know", "i mean",
]
# Markers of a self-correction ("сделай синим, нет, красным").
_CORRECTIONS = ["нет,", "то есть", "вернее", "в смысле", "no wait", "i mean"]


def _letter_words(lower: str) -> list[str]:
    """Tokenise on non-letters. Regex \\b/\\w are unreliable across Cyrillic in
    some engines and would also split on digits differently — this mirrors the
    Swift `split(whereSeparator: { !$0.isLetter && $0 != "-" })` exactly."""
    out: list[str] = []
    current: list[str] = []
    for ch in lower:
        if ch.isalpha() or ch == "-":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def needs_cleanup(text: str) -> bool:
    """Does this transcript actually contain artifacts worth an LLM pass?

    Whisper already punctuates and capitalises, so clean speech can skip the
    ~1 s cleanup entirely — that halves latency for tidy dictations.
    """
    lower = text.lower()
    for marker in _CORRECTIONS:
        if marker in lower:
            return True

    words = _letter_words(lower)
    if not words:
        return False

    word_set = set(words)
    for filler in _FILLERS:
        if " " in filler:
            if filler in lower:
                return True
        elif filler in word_set:
            return True

    for i, word in enumerate(words):
        # Same word twice in a row: "я я думаю".
        if i + 1 < len(words) and word == words[i + 1] and len(word) >= 1:
            return True
        # Same word with one word between — the common stumble shape:
        # "нужно правильно это правильно писать".
        if i + 2 < len(words) and word == words[i + 2] and len(word) >= 3:
            return True
    return False


def predicted_ms(chars: int) -> float:
    """Predicted wall-clock cost of one cleanup round-trip over `chars`.

    Fitted against real dictations, NOT a synthetic benchmark. Generation runs
    at ~40 tok/s and a rewrite emits roughly one token per 4 characters of
    Russian, giving ~6.25 ms per char; a back-to-back curl loop put the fixed
    part at ~400 ms, but in production it is ~900 ms — a benchmark hammering
    Ollama keeps the model and prompt cache maximally hot, while a real
    dictation arrives after a gap. Regression over live runs: 908 + 6.36·chars.

    Underestimating is the expensive direction: at 400 ms the predictor cleared
    work it could not afford, which then ran ~2 s and was discarded at the
    deadline — exactly the waste this is meant to prevent.
    """
    return 900.0 + chars * 6.25


def base_system(vocabulary: str) -> str:
    """The stable system-prompt prefix: base rules + vocabulary. Must be
    byte-identical between warmup() and clean() — Ollama's prompt prefix cache
    is what turns a ~2 s prompt-eval of these ~600 tokens into ~0."""
    system = SYSTEM_PROMPT
    if vocabulary:
        system += (
            "\n\nThe speaker frequently uses these terms/names — prefer them when a "
            f"word was likely misheard: {vocabulary}."
        )
    return system


def mini_system(vocabulary: str) -> str:
    system = MINI_PROMPT
    if vocabulary:
        system += f"\nPrefer these user terms when a word was misheard: {vocabulary}."
    return system


def _kept_enough(before: str, after: str) -> bool:
    """Cleanup must only remove filler/repeats, never eat real content. If the
    model dropped more than ~35% of the words (mis-judged content as filler, or
    truncated), the caller keeps the raw text. Losing punctuation is far better
    than losing sentences."""
    raw_words = word_count(before)
    clean_words = word_count(after)
    return not (raw_words >= 6 and clean_words < int(raw_words * 0.65))


class OllamaCleaner:
    def __init__(self) -> None:
        # Stats of the most recent clean() call, for metrics.
        self.last_chunk_count = 0
        self.last_skipped_sentences = 0
        self.last_over_budget_chunks = 0
        self._last_warmup: float | None = None
        self._warmup_lock = threading.Lock()

    # ── configuration ────────────────────────────────────────────────────

    @property
    def model(self) -> str:
        return config.get_str("FVOllamaModel")

    @property
    def endpoint(self) -> str:
        return config.get_str("FVOllamaHost").rstrip("/") + "/api/chat"

    # ── public API ───────────────────────────────────────────────────────

    def clean(
        self,
        raw: str,
        low_confidence: bool = False,
        vocabulary: str = "",
        budget: float = float("inf"),
    ) -> str:
        """Cleaned text, or the raw text unchanged on any failure.

        `budget` is the wall-clock (seconds) the caller is willing to spend.
        Requests whose predicted cost doesn't fit are skipped rather than
        started, so an over-long transcript pastes immediately instead of
        stalling and then falling back to raw anyway.
        """
        self.last_chunk_count = 0
        self.last_skipped_sentences = 0
        self.last_over_budget_chunks = 0
        if not raw:
            return raw
        # Nothing to fix and the decoder was confident → skip the round-trip.
        if not low_confidence and not needs_cleanup(raw):
            logger.info("cleanup skipped (text already clean)")
            return raw

        # Prefix-cache friendly assembly: the (long) base prompt + vocabulary is
        # byte-identical across calls and primed by warmup(), so Ollama only
        # evaluates the suffix + user text per dictation. The low-confidence
        # hint goes AFTER the stable prefix for the same reason.
        system = base_system(vocabulary)
        if low_confidence:
            system += LOW_CONFIDENCE_HINT

        budget_ms = float("inf") if budget == float("inf") else budget * 1000
        raw_words = word_count(raw)

        # Three latency tiers. Generation cost scales with output length and
        # prompt-eval with prompt length, so: short + confident → mini prompt,
        # full rewrite; medium → full prompt, full rewrite; long → full prompt,
        # only flagged sentences. Low confidence always gets the full prompt and
        # the whole text: garbled words need every rule and all the context.
        if not low_confidence and raw_words < SELECTIVE_THRESHOLD_WORDS:
            if not self._affordable(raw, budget_ms):
                return raw
            return self._clean_whole(raw, mini_system(vocabulary))
        if low_confidence or raw_words < SELECTIVE_THRESHOLD_WORDS:
            if not self._affordable(raw, budget_ms):
                return raw
            return self._clean_whole(raw, system)
        return self._clean_selectively(raw, system, budget_ms)

    def clean_block(self, raw: str, vocabulary: str = "") -> str | None:
        """Clean one confirmed block of a still-running dictation.

        Runs while the user is still speaking, so its latency costs the user
        nothing — that is the whole point of the streaming session's
        incremental cleanup, and why there is no budget check here. Returns
        None when the block needs no cleanup or the LLM failed/mangled it; the
        caller then keeps the raw block, so text is never lost to a failed
        cleanup.
        """
        if not raw or not needs_cleanup(raw):
            return None
        cleaned = self._clean_whole(raw, base_system(vocabulary))
        return None if cleaned == raw else cleaned

    def warmup(self) -> None:
        """Fire-and-forget: load the model AND prime the prompt-prefix cache.

        Metrics showed prompt-eval of the ~600-token prompt costs ~2 s per
        cleanup at Ollama's ~270 tok/s — priming it here, while the user is
        still talking, makes the real call pay only for the user text and the
        generated output. Throttled to once per 10 min AFTER a success
        (keep_alive is 24h); failures retry on the next hotkey press.
        """
        with self._warmup_lock:
            if self._last_warmup and time.monotonic() - self._last_warmup < 600:
                return
        from .dictionary import prompt_string  # local: avoids an import cycle

        vocabulary = prompt_string()
        mini = mini_system(vocabulary)
        system = base_system(vocabulary)

        def run() -> None:
            # Prime BOTH prefixes. Ollama caches per request shape, and short
            # dictations — the common case — go through the mini prompt, which
            # shares no prefix with the full one. Mini first: it serves the
            # latency-critical path.
            if not self._prime(mini):
                return
            self._prime(system)
            with self._warmup_lock:
                self._last_warmup = time.monotonic()  # success only

        threading.Thread(target=run, name="ollama-warmup", daemon=True).start()

    # ── internals ────────────────────────────────────────────────────────

    def _affordable(self, text: str, budget_ms: float) -> bool:
        cost = predicted_ms(len(text))
        if cost <= budget_ms:
            return True
        logger.info(
            "cleanup skipped — %d chars needs ~%.0f ms, budget %.0f ms",
            len(text), cost, budget_ms,
        )
        self.last_over_budget_chunks = 1
        return False

    def _clean_whole(self, raw: str, system: str) -> str:
        cleaned = self._chat(system, raw)
        if not cleaned:
            return raw
        if not _kept_enough(raw, cleaned):
            logger.warning(
                "cleanup dropped too much (%d→%d words), using raw",
                word_count(raw), word_count(cleaned),
            )
            return raw
        return cleaned

    def _clean_selectively(self, raw: str, system: str, budget_ms: float) -> str:
        """Sentence-level cleanup: only flagged sentences (plus up to two clean
        neighbors swallowed into a core) are rewritten; everything else passes
        through untouched. Generated tokens ≈ only the problem spots."""
        parts = [s.strip() for s in sentences(raw)]
        parts = [s for s in parts if s]
        flags = [needs_cleanup(s) for s in parts]
        cores = chunk_cores(flags)
        # needs_cleanup(raw) fired but no single sentence is flagged — the
        # artifact spans a sentence boundary; whole-text handles that.
        if not cores:
            if not self._affordable(raw, budget_ms):
                return raw
            return self._clean_whole(raw, system)

        self.last_skipped_sentences = sum(1 for f in flags if not f)
        # Spend the budget chunk by chunk: an unaffordable chunk is left raw and
        # the next one still gets its chance, so a long dictation degrades
        # gracefully instead of losing all cleanup at the deadline.
        deadline = time.monotonic() + (budget_ms / 1000 if budget_ms != float("inf") else 3600)
        result = list(parts)
        for start, end in cores:
            core_text = " ".join(parts[start : end + 1])
            remaining_ms = (deadline - time.monotonic()) * 1000
            if predicted_ms(len(core_text)) > remaining_ms:
                self.last_over_budget_chunks += 1
                continue  # chunk stays raw — over budget is never a text loss

            user = ""
            if start > 0:
                user += f"<CONTEXT>{parts[start - 1]}</CONTEXT>\n"
            user += f"<FIX>{core_text}</FIX>"
            if end + 1 < len(parts):
                user += f"\n<CONTEXT>{parts[end + 1]}</CONTEXT>"

            self.last_chunk_count += 1
            cleaned = self._chat(system + CHUNK_INSTRUCTION, user)
            # Model echoing tags back = broken format; never splice that in.
            if not cleaned or any(
                tag in cleaned for tag in ("<FIX>", "</FIX>", "<CONTEXT>", "</CONTEXT>")
            ):
                continue  # chunk stays raw — a format break never breaks text
            if not _kept_enough(core_text, cleaned):
                logger.warning(
                    "chunk cleanup dropped too much (%d→%d words), keeping raw chunk",
                    word_count(core_text), word_count(cleaned),
                )
                continue

            result[start] = cleaned
            for i in range(start + 1, end + 1):
                result[i] = ""

        joined = squeeze(" ".join(p for p in result if p))
        # Global safety net on the assembled text as a last line of defense.
        if not _kept_enough(raw, joined):
            return raw
        return joined or raw

    def _chat(self, system: str, user: str) -> str | None:
        """One Ollama chat round-trip. None on any failure — callers fall back
        to the raw text."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            # num_predict -1 = generate until the model naturally stops. A fixed
            # cap here silently TRUNCATED the cleanup of long dictations — the
            # exact "the end is missing" bug. Never cap real output.
            "options": {"temperature": 0.2, "num_predict": -1},
        }
        try:
            payload = self._post(body, timeout=20)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to raw
            logger.warning("Ollama cleanup failed (%s), using raw text", exc)
            return None
        if payload is None:
            return None
        try:
            return payload["message"]["content"].strip()
        except (KeyError, TypeError, AttributeError):
            logger.warning("Ollama returned an unexpected shape, using raw text")
            return None

    def _prime(self, system: str) -> bool:
        """Evaluate (and cache) a system prompt without generating anything."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}],
            "stream": False,
            "keep_alive": KEEP_ALIVE,
            # num_predict 0: evaluate (and cache) the prompt, generate nothing.
            "options": {"temperature": 0, "num_predict": 0},
        }
        try:
            return self._post(body, timeout=60) is not None
        except Exception:  # noqa: BLE001 - warmup is best-effort
            return False

    def _post(self, body: dict, timeout: float) -> dict | None:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    logger.warning("Ollama HTTP %s, falling back to raw text", response.status)
                    return None
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.warning("Ollama HTTP %s, falling back to raw text", exc.code)
            return None
