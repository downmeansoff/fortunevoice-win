"""First-run screen.

A tray app with a global hotkey has a discovery problem: after launch there is
nothing on screen, and if the user doesn't already know the chord, the app is
indistinguishable from one that failed to start. macOS at least raised
permission dialogs that announced the app existed; Windows raises nothing.

So this says the three things that decide whether the first dictation works —
the hotkey, whether the microphone is actually producing signal, and whether
the model finished loading — and proves the microphone with a live meter
instead of claiming it is fine.
"""

from __future__ import annotations

import threading

from .. import audio, config
from ..log import get as get_logger
from . import theme, ui

logger = get_logger("ui.onboarding")

WIDTH = 520
HEIGHT = 480


def needed() -> bool:
    return not config.get_bool("FVOnboarded")


class Onboarding:
    def __init__(self, app=None) -> None:
        self._app = app
        self._window = None
        self._meter = None
        self._meter_fill = None
        self._mic_note = None
        self._model_note = None
        self._level = 0.0
        self._recorder: audio.AudioRecorder | None = None
        self._listening = False

    def show(self, app=None) -> None:
        if app is not None:
            self._app = app
        ui.call(self._show)

    # ── UI thread only ───────────────────────────────────────────────────

    def _show(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._build()
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()
        self._start_meter()

    def _build(self) -> None:
        import tkinter as tk

        from .. import assets

        window = tk.Toplevel(ui.root)
        window.title("FortuneVoice — setup")
        window.geometry(f"{WIDTH}x{HEIGHT}")
        window.resizable(False, False)
        window.configure(bg=theme.INK)
        try:
            window.iconbitmap(str(assets.icon_path()))
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        window.protocol("WM_DELETE_WINDOW", self._done)

        body = tk.Frame(window, bg=theme.INK)
        body.pack(fill="both", expand=True, padx=30, pady=26)

        # Packed FIRST so it reserves its strip at the bottom. `side="bottom"`
        # only claims what is left over at the moment of packing, so building
        # the footer last pushed it off the window entirely.
        footer = tk.Frame(body, bg=theme.INK)
        footer.pack(fill="x", side="bottom")
        theme.button(footer, "Start dictating", self._done, primary=True).pack(side="right")
        theme.label(
            footer, "The tray icon has settings, history and this screen again.",
            size=8, colour=theme.TEXT_FAINT,
        ).pack(side="left", pady=8)

        theme.label(body, "Ready to dictate", size=16, weight="bold").pack(anchor="w")
        theme.label(
            body,
            "Everything runs on this machine. Audio never leaves it.",
            size=9, colour=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 22))

        # ── the hotkey, stated once and prominently ──
        chord = tk.Frame(body, bg=theme.INK_RAISED)
        chord.pack(fill="x")
        inner = tk.Frame(chord, bg=theme.INK_RAISED)
        inner.pack(fill="x", padx=18, pady=16)
        hotkey = self._app.hotkey_label if self._app else config.get_str("FVHotkey")
        theme.label(inner, "Hold to talk", size=9, colour=theme.TEXT_MUTED).pack(anchor="w")
        theme.label(inner, hotkey, size=18, weight="bold", colour=theme.ACCENT).pack(anchor="w")
        theme.label(
            inner, "Hold it, speak, let go. The text is typed where your cursor is.",
            size=9, colour=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(6, 0))

        # ── microphone, proven rather than asserted ──
        theme.label(body, "Microphone", size=11, weight="bold").pack(anchor="w", pady=(22, 6))
        self._mic_note = theme.label(body, "Say something…", size=9, colour=theme.TEXT_MUTED)
        self._mic_note.pack(anchor="w")
        self._meter = tk.Frame(body, bg=theme.INK_RAISED, height=10)
        self._meter.pack(fill="x", pady=(8, 0))
        self._meter_fill = tk.Frame(self._meter, bg=theme.OK, height=10)
        self._meter_fill.place(relwidth=0.0, relheight=1)

        # ── model ──
        theme.label(body, "Model", size=11, weight="bold").pack(anchor="w", pady=(22, 6))
        self._model_note = theme.label(body, "checking…", size=9, colour=theme.TEXT_MUTED)
        self._model_note.pack(anchor="w")

        self._window = window

    # ── live microphone meter ────────────────────────────────────────────

    def _start_meter(self) -> None:
        """Open a *separate* recorder for the meter.

        Deliberately not the app's own: dictation may start while this window
        is open, and two things driving one recorder would fight over the
        session. A second capture stream costs nothing and cannot corrupt a
        real dictation.
        """
        if self._listening:
            return
        self._listening = True

        def run() -> None:
            recorder = audio.AudioRecorder()
            recorder.on_level = self._on_level
            try:
                recorder.start(config.get_str("FVMicrophone"))
            except audio.AudioError as exc:
                logger.warning("onboarding meter could not start: %s", exc)
                ui.call(lambda: self._mic_note.configure(
                    text=f"No microphone: {exc}", fg=theme.ERROR))
                self._listening = False
                return
            self._recorder = recorder

        threading.Thread(target=run, name="onboarding-mic", daemon=True).start()
        self._refresh_model()
        self._tick()

    def _on_level(self, level: float) -> None:
        self._level = level  # audio thread; sampled by the UI timer

    def _tick(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._stop_meter()
            return
        if not self._window.winfo_viewable():
            self._stop_meter()
            return
        # Same scale as the pill, so "loud here" means "loud there".
        value = min(1.0, (self._level / 0.22) ** 0.7)
        self._meter_fill.configure(bg=theme.OK if value > 0.08 else theme.LINE)
        self._meter_fill.place(relwidth=value, relheight=1)
        if value > 0.25:
            self._mic_note.configure(text="Hearing you clearly.", fg=theme.OK)
        self._window.after(50, self._tick)

    def _stop_meter(self) -> None:
        self._listening = False
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:  # noqa: BLE001 - teardown must not raise
                logger.debug("onboarding meter teardown failed", exc_info=True)

    def _refresh_model(self) -> None:
        if self._app is None:
            self._model_note.configure(text="—")
            return
        transcriber = self._app.transcriber
        if transcriber.loaded_model:
            self._model_note.configure(
                text=f"{transcriber.loaded_model} on "
                     f"{transcriber.device}/{transcriber.compute_type} — ready",
                fg=theme.OK,
            )
            return
        self._model_note.configure(
            text="downloading and loading… the first dictation will wait for it",
            fg=theme.TEXT_MUTED,
        )
        self._window.after(1500, self._refresh_model)

    def _done(self) -> None:
        config.set("FVOnboarded", True)
        self._stop_meter()
        if self._window is not None:
            self._window.withdraw()


onboarding = Onboarding()
