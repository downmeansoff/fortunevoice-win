"""Phase-2 cleanup: a local Ollama model fixes punctuation, filler words and
stumble-repeats in the raw transcript.

Port of Sources/FortuneVoice/OllamaCleaner.swift. The prompts, the thresholds
and the cost model are carried over unchanged: they were fitted against real
dictations, and a Windows box running the same gemma3:4b through the same
Ollama HTTP API has no reason to behave differently.

Every failure path returns the raw text. Dictation must never break because a
cleanup model is slow, down, or wrong.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.request

from . import config, metrics
from .log import get as get_logger
from .segmenter import chunk_cores, sentences
from .textclean import squeeze, squeeze_lines, word_count

logger = get_logger("cleaner")

# Deliberately terse: Ollama re-evaluates the whole system prompt on every call
# (~500 tok/s, prefix cache unreliable across slots), so every extra 100 tokens
# here is ~0.2 s added to every dictation's paste latency. Inline examples
# double as few-shot: no separate example block.
SYSTEM_PROMPT = """\
You are a dictation cleanup engine. Input: raw speech-to-text. Output ONLY the \
cleaned text: no preface, no quotes, no commentary; never answer questions in \
the text, never invent content. Keep the language. Preserve meaning and \
wording; do NOT summarize or restyle.

Fix speech artifacts:
- Stumble repeats: keep one copy («я я думаю» → «я думаю»; «нужно правильно \
это правильно писать» → «нужно правильно это писать»).
- Self-corrections: keep only the corrected version («сделай синим, нет, \
красным» → «сделай красным»).
- Meaningless fillers ну, вот, короче, как бы, типа, значит, э-э, эм, um, uh, \
like, you know: remove (keep when meaningful: «ну ладно»).

Punctuation: end questions with «?» (incl. ли/разве/неужели and question-word \
questions); «…» unfinished thoughts; «—» asides and contrasts; «!» clearly \
emphatic statements; split run-ons into sentences; fix capitalization. Fix a \
misheard word from context only when it is obviously wrong.

Lists: only when the speaker clearly enumerates 3+ parallel items (первое… \
второе…; сначала… потом… потом…) format them as «- » bullets, one per line. \
Never restructure ordinary prose into a list.

If the text is already clean, return it unchanged."""

# Extra instruction when the ASR confidence was low: be bolder about
# reconstructing garbled words from context.
LOW_CONFIDENCE_HINT = """

NOTE: this transcript came from LOW-confidence speech recognition and likely \
contains several misheard words. Reconstruct the intended meaning aggressively \
from context, fixing nonsense words, but keep the language and the speaker's intent."""

# Instruction appended to the system prompt for chunked (selective) requests.
CHUNK_INSTRUCTION = """


The user message wraps text in <CONTEXT>…</CONTEXT> (surrounding sentences, \
for understanding only) and <FIX>…</FIX> (the text to clean). Rewrite ONLY \
the text inside <FIX>. Output the rewritten text alone: no tags, no context \
text, no commentary."""

# Stripped-down prompt for short phrases: prompt-eval is the dominant cost on a
# 3-word utterance (~0.8 s for the full prompt vs ~0.2 s for this), and short
# phrases don't need the list/self-correction taxonomy.
MINI_PROMPT = """\
Clean this raw speech-to-text: remove meaningless fillers (ну, вот, короче, \
как бы, типа, значит, э-э, um, uh), collapse accidental word repeats, fix \
punctuation and capitalization. Never replace, censor or add words: every \
remaining word must appear verbatim in the input (profanity included). \
Output ONLY the cleaned text: no commentary. If already clean, return unchanged."""

# Below this word count the whole-text rewrite is used: the LLM round-trip is
# cheap there and whole-text context helps. 25, not 50: generation runs
# ~40 tok/s locally, so a 40-word full rewrite alone costs ~1.4 s; medium
# phrases must go selective so only flagged sentences are regenerated.
SELECTIVE_THRESHOLD_WORDS = 25


def keep_alive() -> str:
    """How long Ollama holds the cleanup model after the last dictation.

    This was pinned at "24h", to spare the next dictation a cold load. On a
    small card that is the wrong trade, and it is felt everywhere rather than
    here: measured on a 6 GB GPU, the resident model holds 2.2 GB, leaving
    ~900 MB with Whisper alongside it. Windows then pages other applications'
    GPU memory over the bus and the whole desktop stutters, reported as
    "everything lags while Ollama is running".

    Measured cost of letting it go: a cold call is 4125 ms against 656 ms warm,
    so 3.5 s once after an idle spell. Most of that is invisible, because
    `warmup()` runs on hotkey-down while the user is still speaking.
    """
    return config.get_str("FVOllamaKeepAlive") or "5m"


def keep_alive_seconds() -> float:
    """`keep_alive()` as a number. Ollama's own spelling: a bare number is
    seconds, a negative one means forever, and "10m"/"2h" are durations."""
    text = keep_alive().strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    try:
        value = float(text[:-1]) * units[text[-1]] if text[-1] in units else float(text)
    except (ValueError, IndexError):
        return 300.0  # unparseable: treat it as the default 5m
    return float("inf") if value < 0 else value


# Filler words that only ever add noise in dictation.
_FILLERS = [
    "ну", "вот", "короче", "как бы", "типа", "значит", "э-э", "ээ", "эм", "мм",
    "um", "uh", "erm", "you know", "i mean",
]
# Markers of a self-correction ("сделай синим, нет, красным").
_CORRECTIONS = ["нет,", "то есть", "вернее", "в смысле", "no wait", "i mean"]


def _letter_words(lower: str) -> list[str]:
    """Tokenise on non-letters. Regex \\b/\\w are unreliable across Cyrillic in
    some engines and would also split on digits differently; this mirrors the
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
    ~1 s cleanup entirely; that halves latency for tidy dictations.
    """
    lower = text.lower()
    for marker in _CORRECTIONS:
        # Word-bounded on the left. "нет," matched as a bare substring fires on
        # «интернет,», «конкурент,», «момент,»: every one of those bought a
        # cleanup round-trip the text did not need.
        position = lower.find(marker)
        while position != -1:
            before = lower[position - 1] if position else " "
            if not (before.isalnum() or before in "-_"):
                return True
            position = lower.find(marker, position + 1)

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
        # Same word with one word between, the common stumble shape:
        # "нужно правильно это правильно писать".
        if i + 2 < len(words) and word == words[i + 2] and len(word) >= 3:
            return True
    return False


# Shipped fit: 908 + 6.36 chars, rounded. Measured against live gemma3:4b
# dictations on Apple silicon, which is the only data that existed when the
# budget was written.
FALLBACK_FIXED_MS = 900.0
FALLBACK_PER_CHAR_MS = 6.25
# Below this many recorded cleanups the fit is noise; keep the shipped one.
MIN_SAMPLES_TO_FIT = 12
# Never trust a learned fit that claims cleanup is nearly free: a handful of
# cache-hot runs can regress to an intercept near zero, and the predictor would
# then clear work it cannot afford - the exact waste it exists to prevent.
MIN_FIXED_MS = 250.0


def predicted_ms(chars: int) -> float:
    """Predicted wall-clock cost of one cleanup round-trip over `chars`.

    Two sources, in order:

    1. **This machine's own history.** `metrics.jsonl` records `cleanup_ms` and
       `chars` for every dictation that ran one, so after a dozen real
       dictations the app knows what its own model, on its own GPU, actually
       costs. This matters: the shipped constants were fitted against
       gemma3:4b on Apple silicon, and qwen2.5:3b on a 3060 answers in ~280 ms
       where they predict 1200+, so the budget was declining work it could
       comfortably afford.
    2. **The shipped fit**, until enough local samples exist.

    Underestimating is the expensive direction - work is started, runs past the
    deadline, and is thrown away - so the learned fit is floored and only
    replaces the default once there is enough data to mean anything.
    """
    fixed, per_char = _fit()
    return fixed + chars * per_char


_fit_cache: tuple[float, float] | None = None
_fit_stamp = 0.0
# Re-read at most this often: the file grows one line per dictation, and
# re-fitting on every call would parse it inside the latency-critical path.
_FIT_TTL = 300.0


def reset_fit() -> None:
    """Forget the learned fit (tests, and after the model is changed)."""
    global _fit_cache, _fit_stamp
    _fit_cache, _fit_stamp = None, 0.0


def _fit() -> tuple[float, float]:
    global _fit_cache, _fit_stamp

    now = time.monotonic()
    if _fit_cache is not None and now - _fit_stamp < _FIT_TTL:
        return _fit_cache
    _fit_stamp = now
    _fit_cache = _fit_from_metrics() or (FALLBACK_FIXED_MS, FALLBACK_PER_CHAR_MS)
    return _fit_cache


def _fit_from_metrics() -> tuple[float, float] | None:
    """Least-squares fit of cleanup_ms against chars, from real dictations.

    Only over runs that are actually comparable. Pooling everything fitted a
    line through three different things at once, and the user's own metrics
    show how badly: 29 chars at 2016 ms (a cold load), 30 chars at 15 ms, and
    642 chars at 437 ms (a chunked run, where the model saw only the flagged
    sentences and `chars` counts the whole transcript). A line through those
    describes nothing, and it gates whether cleanup runs inside a 1.5 s budget.

    So: the same cleanup model, whole-text runs only, and no cold loads.
    """
    wanted = config.get_str("FVOllamaModel")
    try:
        rows = [
            (float(r["chars"]), float(r["cleanup_ms"]))
            for r in metrics.read_all()
            if r.get("cleanup_ms") and r.get("chars")
            # A chunked run's cost belongs to the flagged sentences, not to the
            # whole transcript's length.
            and not r.get("cleanup_chunks")
            # A cold load is ~2 s whatever the text; it is not a per-char cost.
            and not r.get("cleanup_cold")
            # Older rows have no cleanup_model. They predate the field and
            # could be from any model, so they cannot be trusted here.
            and r.get("cleanup_model") == wanted
        ]
    except Exception:  # noqa: BLE001 - a missing or corrupt file is not fatal
        return None
    if len(rows) < MIN_SAMPLES_TO_FIT:
        return None

    n = len(rows)
    sum_x = sum(x for x, _ in rows)
    sum_y = sum(y for _, y in rows)
    sum_xx = sum(x * x for x, _ in rows)
    sum_xy = sum(x * y for x, y in rows)
    denominator = n * sum_xx - sum_x * sum_x
    if denominator <= 0:
        return None  # every sample the same length; nothing to fit
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    if slope <= 0:
        return None  # longer text coming out cheaper is noise, not a model
    fixed = max(MIN_FIXED_MS, intercept)
    logger.debug("cleanup cost fitted from %d runs: %.0f + %.2f/char",
                 n, fixed, slope)
    return fixed, slope


def installed_models() -> list[str]:
    """Model names Ollama currently has pulled.

    Used to populate the Settings dropdown: offering a model that is not
    installed produces a setting that looks applied and then fails at the
    first dictation.
    """
    host = config.get_str("FVOllamaHost").rstrip("/")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=2.0) as response:
            if response.status != 200:
                return []
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - Ollama not running is the normal case
        return []
    names = [m.get("name", "") for m in payload.get("models", [])]
    return sorted(name for name in names if name)


def base_system(vocabulary: str) -> str:
    """The stable system-prompt prefix: base rules + vocabulary. Must be
    byte-identical between warmup() and clean(): Ollama's prompt prefix cache
    is what turns a ~2 s prompt-eval of these ~600 tokens into ~0."""
    system = SYSTEM_PROMPT
    if vocabulary:
        system += (
            "\n\nThe speaker frequently uses these terms/names: prefer them when a "
            f"word was likely misheard: {vocabulary}."
        )
    return system


def mini_system(vocabulary: str) -> str:
    system = MINI_PROMPT
    if vocabulary:
        system += f"\nPrefer these user terms when a word was misheard: {vocabulary}."
    return system


# Words whose loss flips the meaning of the sentence. Dropping one is not a
# tidy-up, and every other word survives, so neither the ratio below nor the
# invented-content check would notice.
_NEGATIONS = {"не", "нет", "ни", "нельзя", "никак", "никогда",
              "not", "no", "never", "cannot", "dont", "doesnt", "didnt",
              "cant", "wont", "isnt", "arent", "wasnt", "werent", "havent",
              "hasnt", "hadnt", "shouldnt", "wouldnt", "couldnt"}

# English hides most of its negations in a contraction, and `_letter_words`
# splits on non-letters, so "don't" arrived as "don" + "t" and matched
# nothing at all. "i don't think we can ship this", cleaned to "I think we can
# ship this", passed every guard: same words, one shorter, no negation counted
# on either side. Counted directly instead, before tokenising, and the stem is
# then blanked so "ca" and "wo" cannot be mistaken for words.
_CONTRACTED_NOT = re.compile("n['\u2019\u02bc]t\\b")


def _negations(text: str) -> int:
    lowered = text.lower()
    contracted = len(_CONTRACTED_NOT.findall(lowered))
    rest = _CONTRACTED_NOT.sub(" ", lowered)
    return contracted + sum(1 for word in _letter_words(rest) if word in _NEGATIONS)


def _kept_enough(before: str, after: str) -> bool:
    """Cleanup must only remove filler/repeats, never eat real content.

    Two rules, because one ratio cannot cover both lengths:

    * **Six words and up**: more than ~35% of the words gone means the model
      mis-judged content as filler, or truncated. Losing punctuation is far
      better than losing sentences.
    * **Below six**: the ratio was simply switched off, so "нет я не согласен
      совсем" could come back as "согласен" and pass: every remaining word
      does appear in the raw, so nothing else objected. But a short dictation
      legitimately loses most of itself ("ну вот привет" → "Привет"), so the
      test is not a ratio: every word that is NOT filler has to survive.
    """
    raw_words = word_count(before)
    clean_words = word_count(after)
    if raw_words >= 6:
        # Rounded UP. `int()` truncates, and the shortest text this branch
        # handles is where that hurts most: six words allowed a drop to three,
        # which is half the dictation gone through a guard whose stated limit
        # is about a third, and six words is exactly where the strict
        # every-word-survives rule below stops applying.
        return clean_words >= math.ceil(raw_words * 0.65)

    kept = {word for word in _letter_words(after.lower())}
    for word in _letter_words(before.lower()):
        if word in _FILLERS:
            continue
        if word not in kept:
            return False
    return True


# How much of the cleaned text may be words the speaker never said. Some slack
# is needed: the model legitimately re-inflects ("отчете" → "отчёте") and joins
# clitics, and a stem comparison does not catch every such case.
INVENTED_WORD_LIMIT = 0.3
# Words are matched on their first N characters so Russian inflection does not
# read as invention. 4 is long enough to separate "красн-" from "красив-".
_STEM = 4


def _stems(text: str) -> list[str]:
    return [w[:_STEM] for w in _letter_words(text.lower()) if len(w) > 2]


def _no_invented_content(before: str, after: str) -> bool:
    """Did the model put words in the user's mouth?

    The 35% guard above only catches *deletion*. Substitution slips straight
    through it, and substitution is the worse failure: measured on this
    machine, qwen2.5:1.5b turned "надо бы проверить, как работает диктовка"
    into "нужно правильно это писать": same word count, entirely different
    sentence, and the app would have typed it.

    Nothing else in the pipeline can catch this. Whisper's own output is the
    only record of what was actually said, so the check has to happen here,
    against it.
    """
    after_stems = _stems(after)
    if not after_stems:
        return True
    before_stems = set(_stems(before))
    invented = sum(1 for stem in after_stems if stem not in before_stems)
    return invented <= len(after_stems) * INVENTED_WORD_LIMIT


def _unbullet(text: str) -> str:
    """Drop a leading list marker the model added on its own.

    gemma3:4b answers a one-sentence cleanup with "- Мы вроде как…". The
    content is right; the dash is the model formatting an answer rather than
    returning the sentence, and it would be typed into the user's document
    verbatim. Only a single leading marker goes: a dictation that genuinely
    starts with a dash keeps it, because that one is followed by more lines.
    """
    stripped = text.lstrip()
    if "\n" in stripped:
        return text  # a real list: leave it alone
    for marker in ("- ", "– ", "— ", "* ", "• "):
        if stripped.startswith(marker):
            return stripped[len(marker):].lstrip()
    return text


def _is_safe(before: str, after: str) -> bool:
    """Every content guard in one place, so no call site can remember one and
    forget another."""
    if not _kept_enough(before, after):
        return False
    if not _no_invented_content(before, after):
        logger.warning("cleanup invented content, using raw text")
        return False
    # A lost negation inverts the sentence, and neither guard above would see
    # it: every remaining word appears in the raw, and one word out of twelve
    # is well inside the ratio. "мы не будем это делать" becoming "мы будем
    # это делать" is the worst thing cleanup can do: it does not garble the
    # text, it makes it confidently say the opposite.
    # Not `<`. A negation the model ADDS inverts the sentence just as
    # thoroughly, and the invented-content guard does not see it when the word
    # already appears somewhere in the raw: "не знаю что делать" coming back
    # as "Не знаю, что не делать" passed.
    if _negations(after) != _negations(before):
        logger.warning("cleanup changed a negation, using raw text")
        return False
    return True


def _device_options(options: dict) -> dict:
    """`options` with the chosen device applied.

    Every request has to carry it, warmup included: priming loads the
    model, and priming without this pulled 1.9 GB onto the card the user
    had just told the app to leave alone: the setting appeared to work
    while the memory went exactly where it was not wanted.
    """
    if config.get_str("FVCleanupDevice").lower() == "cpu":
        # Ollama reads num_gpu as "layers to offload"; zero keeps the
        # whole model in system memory and leaves the card to Whisper.
        return {**options, "num_gpu": 0}
    return options


class OllamaCleaner:
    def __init__(self) -> None:
        # Stats of the most recent clean() call, for metrics.
        self.last_chunk_count = 0
        self.last_skipped_sentences = 0
        self.last_over_budget_chunks = 0
        self._last_warmup: float | None = None
        self._warmup_lock = threading.Lock()
        # Set by note_cold() before each dictation's cleanup.
        self.last_was_cold = False

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
        # The mini tier is a latency trade, so it is only worth taking when the
        # model is slow enough for prompt-eval to dominate. Measured here, the
        # mini prompt costs a model that answers in ~300 ms most of its
        # accuracy and buys nothing back.
        use_mini = config.get_bool("FVMiniPrompt")
        if use_mini and not low_confidence and raw_words < SELECTIVE_THRESHOLD_WORDS:
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
        nothing: that is the whole point of the streaming session's
        incremental cleanup, and why there is no budget check here. Returns
        None when the block needs no cleanup or the LLM failed/mangled it; the
        caller then keeps the raw block, so text is never lost to a failed
        cleanup.
        """
        if not raw or not needs_cleanup(raw):
            return None
        cleaned = self._clean_whole(raw, base_system(vocabulary))
        return None if cleaned == raw else cleaned

    def note_cold(self) -> None:
        """Record whether the cleanup model had to be loaded for this run.

        Read once per dictation, before the call, because afterwards it is
        resident either way. A cold load costs ~2 s whatever the text, so a run
        that paid one must not be fitted as a per-character cost.
        """
        self.last_was_cold = not self._model_is_resident()

    def _model_is_resident(self) -> bool:
        """Is the cleanup model loaded in Ollama right now?

        `/api/ps` lists what is actually in memory. Deliberately a short
        timeout: this runs on hotkey-down and the answer is only used to skip
        work, so "don't know" must mean "prime anyway" rather than "wait".
        """
        host = config.get_str("FVOllamaHost").rstrip("/")
        try:
            with urllib.request.urlopen(f"{host}/api/ps", timeout=1.0) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - Ollama down is the normal case
            return False
        wanted = self.model
        return any(entry.get("name") == wanted or entry.get("model") == wanted
                   for entry in payload.get("models", []))

    def warmup(self) -> None:
        """Fire-and-forget: load the model AND prime the prompt-prefix cache.

        Metrics showed prompt-eval of the ~600-token prompt costs ~2 s per
        cleanup at Ollama's ~270 tok/s; priming it here, while the user is
        still talking, makes the real call pay only for the user text and the
        generated output.

        Skipped only when a recent prime succeeded AND the model is still
        resident. The time check alone was not enough: it assumed `keep_alive`
        of 24h meant the model stays loaded, and that assumption breaks in the
        two situations that matter most: Ollama being restarted, and Ollama
        evicting the model under VRAM pressure, which is exactly what happens
        on a small card when Whisper loads beside it. A measured dictation paid
        2016 ms against a 1500 ms budget because warmup had silently
        short-circuited on a model that was no longer there.
        """
        with self._warmup_lock:
            recently = bool(self._last_warmup
                            and time.monotonic() - self._last_warmup < 600)
        if recently and self._model_is_resident():
            return
        from .dictionary import prompt_string  # local: avoids an import cycle

        vocabulary = prompt_string()
        mini = mini_system(vocabulary)
        system = base_system(vocabulary)

        prime_mini = config.get_bool("FVMiniPrompt")

        def run() -> None:
            # Start Ollama if it is not up. This is the right moment for it:
            # warmup runs on hotkey-down, so the several seconds the server
            # needs are spent while the user is still speaking, instead of
            # being paid (or silently skipped) after they let go.
            from . import ollama_service

            if not ollama_service.ensure_running():
                return
            # Prime BOTH prefixes when both are in use. Ollama caches per
            # request shape, and short dictations (the common case) go
            # through the mini prompt, which shares no prefix with the full
            # one. Mini first: it serves the latency-critical path. With
            # FVMiniPrompt off, that prefix is never used and priming it would
            # just be one more round-trip on hotkey-down.
            if prime_mini and not self._prime(mini):
                return
            if not self._prime(system):
                return
            with self._warmup_lock:
                self._last_warmup = time.monotonic()  # success only

        threading.Thread(target=run, name="ollama-warmup", daemon=True).start()

    # ── internals ────────────────────────────────────────────────────────

    def _affordable(self, text: str, budget_ms: float) -> bool:
        cost = predicted_ms(len(text))
        if cost <= budget_ms:
            return True
        logger.info(
            "cleanup skipped: %d chars needs ~%.0f ms, budget %.0f ms",
            len(text), cost, budget_ms,
        )
        self.last_over_budget_chunks = 1
        return False

    def _clean_whole(self, raw: str, system: str) -> str:
        cleaned = self._chat(system, raw)
        if not cleaned:
            return raw
        if not _is_safe(raw, cleaned):
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
        # needs_cleanup(raw) fired but no single sentence is flagged: the
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
                continue  # chunk stays raw: over budget is never a text loss

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
                continue  # chunk stays raw: a format break never breaks text
            if not _is_safe(core_text, cleaned):
                logger.warning(
                    "chunk cleanup dropped too much (%d→%d words), keeping raw chunk",
                    word_count(core_text), word_count(cleaned),
                )
                continue
            # Second line of defence against a model that copies the
            # <CONTEXT> text back without the tags, returning a chunk that
            # carries its neighbour's sentence: the words are all in the raw
            # and the chunk is longer, not shorter, so the per-chunk guards
            # above pass it. The assembled text is still checked as a whole,
            # and in every case reproduced here that global check caught it;
            # this makes the rejection local, so one bad chunk costs its own
            # cleanup instead of the whole dictation's. Cleanup removes filler
            # and punctuation; it has no business growing a chunk by half.
            if word_count(cleaned) > word_count(core_text) * 1.5 + 3:
                logger.warning(
                    "chunk cleanup grew (%d→%d words), keeping raw chunk",
                    word_count(core_text), word_count(cleaned),
                )
                continue

            result[start] = cleaned
            for i in range(start + 1, end + 1):
                result[i] = ""

        # Lines survive the rejoin: the system prompt asks the model to format
        # an enumeration as «- » bullets, one per line, and `squeeze` collapses
        # every whitespace run (newlines included), so the list came back as
        # one flat line.
        joined = squeeze_lines(" ".join(p for p in result if p))
        # Global safety net on the assembled text as a last line of defense.
        if not _is_safe(raw, joined):
            return raw
        return joined or raw

    def _chat(self, system: str, user: str) -> str | None:
        """One Ollama chat round-trip. None on any failure; callers fall back
        to the raw text."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": keep_alive(),
            # num_predict -1 = generate until the model naturally stops. A fixed
            # cap here silently TRUNCATED the cleanup of long dictations: the
            # exact "the end is missing" bug. Never cap real output.
            "options": _device_options({"temperature": 0.2, "num_predict": -1}),
        }
        try:
            payload = self._post(body, timeout=20)
        except Exception as exc:  # noqa: BLE001 - any failure degrades to raw
            logger.warning("Ollama cleanup failed (%s), using raw text", exc)
            return None
        if payload is None:
            return None
        try:
            return _unbullet(payload["message"]["content"].strip())
        except (KeyError, TypeError, AttributeError):
            logger.warning("Ollama returned an unexpected shape, using raw text")
            return None

    def _prime(self, system: str) -> bool:
        """Evaluate (and cache) a system prompt without generating anything."""
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}],
            "stream": False,
            "keep_alive": keep_alive(),
            # num_predict 0: evaluate (and cache) the prompt, generate nothing.
            "options": _device_options({"temperature": 0, "num_predict": 0}),
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
