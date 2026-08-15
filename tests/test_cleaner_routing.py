"""Which prompt tier a transcript is routed to.

The routing is invisible at runtime — a dictation just comes back better or
worse — so it is worth pinning down. Measured on this machine, the mini tier
costs a fast model most of its accuracy (see PORT_NOTES.md), which is why
FVMiniPrompt exists; these tests make sure the flag actually changes the
request and that nothing else about the routing moved.
"""

from __future__ import annotations

import pytest

from fortunevoice import cleaner as C
from fortunevoice import config

SHORT = "ну я это, короче, хотел сказать что завтра встреча"  # 9 words
LONG = " ".join(["ну это самое, короче, надо бы"] * 12)  # > 25 words


@pytest.fixture()
def recorded(monkeypatch):
    """Capture the system prompt of every chat() call instead of hitting Ollama."""
    calls: list[tuple[str, str]] = []

    def fake_chat(self, system, user):  # noqa: ANN001
        calls.append((system, user))
        return user  # echo: unchanged text, so no safety net trips

    monkeypatch.setattr(C.OllamaCleaner, "_chat", fake_chat, raising=True)
    return calls


def tier(system: str) -> str:
    if system.startswith(C.MINI_PROMPT):
        return "mini"
    if C.CHUNK_INSTRUCTION in system:
        return "selective"
    return "full"


def test_short_text_uses_the_mini_prompt_by_default(recorded):
    C.OllamaCleaner().clean(SHORT)
    assert [tier(s) for s, _ in recorded] == ["mini"]


def test_mini_prompt_can_be_turned_off(recorded):
    config.set("FVMiniPrompt", False)
    C.OllamaCleaner().clean(SHORT)
    assert [tier(s) for s, _ in recorded] == ["full"]


def test_long_text_still_goes_selective_with_the_flag_off(recorded):
    """The flag governs the mini tier only. Long transcripts must keep being
    cleaned sentence by sentence — sending the whole thing would blow the
    budget, which is what the selective path exists to avoid."""
    config.set("FVMiniPrompt", False)
    C.OllamaCleaner().clean(LONG, budget=float("inf"))
    assert recorded, "a long transcript with artifacts must still be cleaned"
    assert all(tier(s) == "selective" for s, _ in recorded)


def test_low_confidence_ignores_the_flag(recorded):
    """Garbled text always gets the full prompt and the whole transcript —
    reconstructing misheard words needs every rule and all the context."""
    C.OllamaCleaner().clean(SHORT, low_confidence=True)
    assert [tier(s) for s, _ in recorded] == ["full"]
    assert C.LOW_CONFIDENCE_HINT in recorded[0][0]


def test_clean_text_never_reaches_the_model(recorded):
    C.OllamaCleaner().clean("Завтра встреча в десять утра.")
    assert recorded == []


def test_over_budget_declines_before_calling(recorded):
    config.set("FVMiniPrompt", False)
    # predicted_ms(len) = 900 + 6.25*chars; a 0.2 s budget can afford nothing.
    result = C.OllamaCleaner().clean(SHORT, budget=0.2)
    assert result == SHORT
    assert recorded == []


# ── warm-up skipping ─────────────────────────────────────────────────────


def test_warmup_skips_only_when_the_model_is_really_loaded(monkeypatch):
    """The bug this fixes: warmup() skipped for 10 minutes after a success,
    assuming keep_alive=24h meant the model stayed. Ollama restarting, or
    evicting it under VRAM pressure — which is what happens on a small card
    when Whisper loads beside it — broke that assumption silently, and the next
    dictation paid a 2 s cold load inside a 1.5 s budget."""
    import time

    from fortunevoice import ollama

    # About the priming throttle, not about starting Ollama — which warmup now
    # also does, and which conftest makes fail fast.
    monkeypatch.setattr(ollama, "ensure_running", lambda wait=0: True)

    primed: list[str] = []
    monkeypatch.setattr(C.OllamaCleaner, "_prime",
                        lambda self, system: primed.append(system) or True)

    cleaner = C.OllamaCleaner()
    cleaner._last_warmup = time.monotonic()  # "primed a moment ago"

    monkeypatch.setattr(C.OllamaCleaner, "_model_is_resident", lambda self: True)
    cleaner.warmup()
    assert primed == [], "a loaded model needs no re-priming"

    monkeypatch.setattr(C.OllamaCleaner, "_model_is_resident", lambda self: False)
    cleaner.warmup()
    for _ in range(200):
        if primed:
            break
        time.sleep(0.01)
    assert primed, "an evicted model must be primed again despite the throttle"


def test_residency_check_treats_a_dead_ollama_as_not_loaded(monkeypatch):
    """`/api/ps` unreachable must mean "prime anyway", never "wait" — the call
    sits on hotkey-down and its answer is only used to skip work."""
    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(C.urllib.request, "urlopen", boom)
    assert C.OllamaCleaner()._model_is_resident() is False
