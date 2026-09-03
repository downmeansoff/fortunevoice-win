"""First-run screen.

A tray app with a global hotkey has a discovery problem: after launch there is
nothing on screen, and if the user doesn't already know the chord, the app is
indistinguishable from one that failed to start. macOS at least raised
permission dialogs that announced the app existed; Windows raises nothing.

So this says the three things that decide whether the first dictation works:
the hotkey, whether the microphone is actually producing signal, and whether
the model finished loading, and proves the microphone with a live meter
instead of claiming it is fine.
"""

from __future__ import annotations

import threading

from .. import audio, config, winapi
from ..log import get as get_logger
from ..strings import t
from . import theme, ui, widgets

logger = get_logger("ui.onboarding")

WIDTH = theme.px(520)
# Sized to the content rather than guessed: the previous height left a
# band of dead space between the last section and the footer, which is
# what an unfinished window looks like.
HEIGHT = theme.px(430)


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
        window.title(t("setup.title"))
        window.geometry(f"{WIDTH}x{HEIGHT}")
        window.resizable(False, False)
        window.configure(bg=theme.INK)
        try:
            window.iconbitmap(str(assets.icon_path()))
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        window.protocol("WM_DELETE_WINDOW", self._done)
        window.update_idletasks()
        winapi.use_dark_titlebar(window)

        body = tk.Frame(window, bg=theme.INK)
        body.pack(fill="both", expand=True, padx=theme.px(30), pady=theme.px(24))

        # Packed FIRST so it reserves its strip at the bottom. `side="bottom"`
        # only claims what is left over at the moment of packing, so building
        # the footer last pushed it off the window entirely.
        footer = tk.Frame(body, bg=theme.INK)
        footer.pack(fill="x", side="bottom", pady=(theme.px(18), 0))
        theme.button(footer, t("setup.start"), self._done, primary=True).pack(side="right")
        theme.label(
            footer, t("setup.tray_hint"),
            size=8, colour=theme.TEXT_FAINT,
        ).pack(side="left", pady=8)

        theme.label(body, t("setup.heading"), size=18, display=True).pack(anchor="w")
        theme.label(
            body,
            t("setup.privacy"),
            size=9, colour=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(theme.px(4), theme.px(18)))

        # ── the hotkey, stated once and prominently ──
        chord = tk.Frame(body, bg=theme.CARD)
        chord.pack(fill="x")
        inner = tk.Frame(chord, bg=theme.CARD)
        inner.pack(fill="x", padx=theme.px(18), pady=theme.px(16))
        # The pretty label either way. Without an app this fell back to the
        # raw config string, so the first thing a new user reads (in the
        # largest type on the screen) was "ctrl+alt+space" rather than the
        # "Ctrl+Alt+Space" every other surface shows.
        if self._app is not None:
            hotkey = self._app.hotkey_label
        else:
            from ..hotkey import parse as parse_hotkey

            raw = config.get_str("FVHotkey")
            try:
                hotkey = parse_hotkey(raw).label
            except ValueError:
                hotkey = raw
        theme.label(inner, t("setup.hold_to_talk"), size=9,
                    colour=theme.TEXT_MUTED).pack(anchor="w")
        # Recordable, not stated. This is the largest type on the screen and
        # it was a Label, so the single most likely thing a new user needs to
        # change on first run, because ctrl+alt+space collides with their IME
        # or their launcher, was the one thing presented as immutable. They
        # had to dismiss setup, find the tray, open the window and scroll to
        # the second row.
        recorder = widgets.ShortcutRecorder(
            inner, lambda: config.get_str("FVHotkey"), self._save_hotkey,
            capture=self._app.capture_chord if self._app is not None else None)
        recorder.pack(anchor="w", pady=(theme.px(4), 0))
        recorder.bind_clickable(inner)
        self._recorder = recorder
        theme.label(
            inner, t("setup.how"),
            size=9, colour=theme.TEXT_MUTED,
        ).pack(anchor="w", pady=(theme.px(6), 0))

        # ── microphone, proven rather than asserted ──
        theme.label(body, t("setup.microphone"), size=11, weight="bold").pack(
            anchor="w", pady=(theme.px(20), theme.px(5)))
        self._mic_note = theme.label(body, t("setup.say_something"), size=9,
                                     colour=theme.TEXT_MUTED)
        self._mic_note.pack(anchor="w")
        # Drawn on a canvas so the track and the fill can both be rounded; a
        # flat Frame reads as a progress bar someone forgot to style.
        self._meter = tk.Canvas(body, height=theme.px(12), bg=theme.INK,
                                highlightthickness=0, bd=0)
        self._meter.pack(fill="x", pady=(theme.px(8), 0))

        # The answer for when the meter stays flat. This screen exists
        # to prove the microphone works, and when it did not it had
        # nothing to offer: no way to choose another device, and no
        # hint that the tray has a submenu for it.
        from .main_window import _microphones

        picker = tk.Frame(body, bg=body["bg"])
        picker.pack(fill="x", pady=(theme.px(10), 0))
        widgets.Dropdown(picker, _microphones(),
                         lambda: config.get_str("FVMicrophone"),
                         self._pick_microphone,
                         refresh=_microphones).pack(side="left")
        self._meter.bind("<Configure>", lambda _e: self._paint_meter())
        self._meter_level = 0.0

        # ── model ──
        theme.label(body, t("setup.model"), size=11, weight="bold").pack(
            anchor="w", pady=(theme.px(20), theme.px(5)))
        self._model_note = theme.label(body, t("ollama.checking"), size=9,
                                       colour=theme.TEXT_MUTED)
        self._model_note.pack(anchor="w")

        self._window = window

    # ── live microphone meter ────────────────────────────────────────────

    def _save_hotkey(self, value: str) -> bool:
        """Store and rebind, exactly as the Settings row does.

        Applying it live is the point on this screen above all others: the
        user is about to try it for the first time, and a shortcut they cannot
        test until after a restart is one they cannot tell is wrong.
        """
        from ..hotkey import parse as parse_hotkey

        value = (value or "").strip()
        if not value or value == config.get_str("FVHotkey"):
            return False
        try:
            parse_hotkey(value)
        except ValueError:
            return False
        config.set("FVHotkey", value)
        if self._app is not None:
            self._app.rebind_hotkey()
        return True

    def _pick_microphone(self, name: str) -> None:
        """Switch device and prove the new one immediately.

        The whole screen is built around "the microphone works, look at the
        meter". When it did not, there was no way to choose another from here
        at all: the user's only recourse was to already know the tray has a
        Microphone submenu.
        """
        config.set("FVMicrophone", name)
        self._stop_meter()
        self._start_meter()

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
                    text=t("setup.no_microphone", error=exc), fg=theme.ERROR))
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
        self._meter_level = min(1.0, (self._level / 0.22) ** 0.7)
        self._paint_meter()
        if self._meter_level > 0.25:
            self._mic_note.configure(text=t("setup.hearing_you"), fg=theme.OK)
        self._window.after(50, self._tick)

    def _paint_meter(self) -> None:
        width = self._meter.winfo_width()
        height = self._meter.winfo_height()
        if width < 4 or height < 2:
            return
        radius = height // 2
        self._meter.delete("all")
        # The track is a step lighter than a card: at CARD on INK it was so
        # close to the background that the meter looked like a hairline.
        theme.rounded_rect(self._meter, 0, 0, width - 1, height - 1, radius,
                           fill=theme.CARD_HI)
        filled = int((width - 1) * self._meter_level)
        if filled > radius:
            theme.rounded_rect(self._meter, 0, 0, filled, height - 1, radius,
                               fill=theme.OK if self._meter_level > 0.08 else theme.LINE)

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
            self._model_note.configure(text="-")
            return
        transcriber = self._app.transcriber
        if transcriber.loaded_model:
            self._model_note.configure(
                text=t("setup.model_ready", model=transcriber.loaded_model,
                       device=f"{transcriber.device}/{transcriber.compute_type}"),
                fg=theme.OK,
            )
            return
        self._model_note.configure(
            text=t("setup.model_loading"),
            fg=theme.TEXT_MUTED,
        )
        self._window.after(1500, self._refresh_model)

    def _done(self) -> None:
        """Dismiss setup and show the app itself.

        Closing to nothing was the wrong ending: the tray icon is the only way
        back in, and a user who has just been told "you're ready" and then
        sees an empty desktop reasonably concludes the app closed. Opening the
        main window once, here, is the moment to show what they actually got.
        """
        config.set("FVOnboarded", True)
        self._stop_meter()
        if self._window is not None:
            self._window.withdraw()
        if self._app is not None:
            self._app.open_main_window()


onboarding = Onboarding()
