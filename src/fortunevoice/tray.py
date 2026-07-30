"""Tray icon and menu — the app's only visible surface.

macOS put this in the menubar with SF Symbols and a tinted template image.
Windows tray icons are 16×16 bitmaps with no tinting, so the state is carried
by colour instead: grey idle, red recording, amber transcribing, yellow error.

pystray owns the main thread (it runs a Win32 message loop), which is why
App's own work all happens on background threads.
"""

from __future__ import annotations

import threading

from . import audio, config, dictionary
from .app import App, State
from .log import get as get_logger

logger = get_logger("tray")

_COLOURS = {
    State.LOADING: (140, 140, 145),
    State.IDLE: (110, 190, 130),
    State.RECORDING: (225, 75, 75),
    State.PROCESSING: (240, 170, 60),
    State.ERROR: (235, 205, 70),
}

_LABELS = {
    State.LOADING: "Loading the model…",
    State.IDLE: "Idle",
    State.RECORDING: "Recording…",
    State.PROCESSING: "Transcribing…",
    State.ERROR: "Model failed to load",
}

LANGUAGES = [("ru", "Русский"), ("en", "English"), ("auto", "Auto-detect")]


def _icon_image(state: State):
    from PIL import Image, ImageDraw  # noqa: PLC0415

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour = _COLOURS.get(state, (140, 140, 145))
    draw.ellipse((6, 6, size - 6, size - 6), fill=colour + (255,))
    if state == State.RECORDING:
        # A filled ring reads as "live" at 16 px where a glyph would not.
        draw.ellipse((20, 20, size - 20, size - 20), fill=(255, 255, 255, 235))
    return image


class Tray:
    def __init__(self, app: App) -> None:
        self.app = app
        self._icon = None
        app.on_state_change = self._on_state_change
        app.on_notify = self._notify

    # ── menu ─────────────────────────────────────────────────────────────

    def _menu(self):
        import pystray  # noqa: PLC0415

        item = pystray.MenuItem
        separator = pystray.Menu.SEPARATOR

        language_items = [
            item(
                title,
                self._select_language(code),
                checked=lambda _i, code=code: config.get_str("FVLanguage") == code,
                radio=True,
            )
            for code, title in LANGUAGES
        ]

        return pystray.Menu(
            item(lambda _i: self._status_text(), None, enabled=False),
            item(lambda _i: f"Hold {self.app.hotkey_label} to dictate", None, enabled=False),
            separator,
            item("Language", pystray.Menu(*language_items)),
            item("Microphone", pystray.Menu(self._microphone_items)),
            item(
                "AI cleanup (Ollama)",
                self._toggle("FVCleanupEnabled"),
                checked=lambda _i: config.get_bool("FVCleanupEnabled"),
            ),
            item(
                "Auto-fix garbled words",
                self._toggle("FVSmartFix"),
                checked=lambda _i: config.get_bool("FVSmartFix"),
            ),
            separator,
            item("Copy last dictation", self._copy_last,
                 enabled=lambda _i: bool(self.app.last_transcript)),
            item(
                lambda _i: self._recover_text(),
                self._recover,
                visible=lambda _i: bool(self.app.recovery.pending()),
            ),
            item("Retry model load", self._reload,
                 visible=lambda _i: self.app.state is State.ERROR),
            item("Open data folder", self._open_folder),
            separator,
            item("Quit FortuneVoice", self._quit),
        )

    def _microphone_items(self):
        import pystray  # noqa: PLC0415

        item = pystray.MenuItem
        yield item(
            "System default",
            self._select_microphone(""),
            checked=lambda _i: not config.get_str("FVMicrophone"),
            radio=True,
        )
        for _index, name in audio.input_devices():
            yield item(
                name[:48],
                self._select_microphone(name),
                checked=lambda _i, name=name: config.get_str("FVMicrophone") == name,
                radio=True,
            )

    def _status_text(self) -> str:
        state = self.app.state
        label = _LABELS.get(state, state.value)
        if self.app.status_note:
            label = f"{label} — {self.app.status_note}"
        if state is State.IDLE and self.app.transcriber.loaded_model:
            label = (
                f"{label} · {self.app.transcriber.loaded_model}"
                f" ({self.app.transcriber.device})"
            )
        return f"FortuneVoice — {label}"

    # ── actions ──────────────────────────────────────────────────────────

    def _select_language(self, code: str):
        def action(_icon, _item) -> None:
            config.set("FVLanguage", code)
            self.app.transcriber.reset_session_language()

        return action

    def _select_microphone(self, name: str):
        def action(_icon, _item) -> None:
            config.set("FVMicrophone", name)

        return action

    def _toggle(self, key: str):
        def action(_icon, _item) -> None:
            if config.toggle(key):
                self.app.cleaner.warmup()

        return action

    def _copy_last(self, _icon=None, _item=None) -> None:
        if not self.app.copy_last():
            logger.warning("could not copy the last dictation")

    def _recover_text(self) -> str:
        count = len(self.app.recovery.pending())
        return "Recover failed dictation" + (f" ({count})" if count > 1 else "")

    def _recover(self, _icon=None, _item=None) -> None:
        self.app.recover_failed()

    def _reload(self, _icon=None, _item=None) -> None:
        self.app.reload_model()

    def _open_folder(self, _icon=None, _item=None) -> None:
        self.app.open_data_folder()

    def _quit(self, _icon=None, _item=None) -> None:
        self.app.stop()
        if self._icon:
            self._icon.stop()

    # ── plumbing ─────────────────────────────────────────────────────────

    def _on_state_change(self, state: State) -> None:
        if not self._icon:
            return
        try:
            self._icon.icon = _icon_image(state)
            self._icon.title = self._status_text()
            # Menu labels are computed lazily, but the tooltip and icon are not
            # — nudge pystray to redraw.
            self._icon.update_menu()
        except Exception:  # noqa: BLE001 - a tray hiccup must not break dictation
            logger.debug("could not update the tray icon", exc_info=True)

    def _notify(self, title: str, body: str) -> None:
        if not self._icon:
            return

        def show() -> None:
            try:
                self._icon.notify(body, title)
            except Exception:  # noqa: BLE001 - balloons fail on locked sessions
                logger.debug("notification failed", exc_info=True)

        threading.Thread(target=show, name="tray-notify", daemon=True).start()

    def run(self) -> None:
        """Blocks on the main thread until Quit."""
        import pystray  # noqa: PLC0415

        # Touch the dictionary once so a malformed file is reported at startup
        # rather than in the middle of the first dictation.
        dictionary.prompt_string()

        self._icon = pystray.Icon(
            "FortuneVoice",
            icon=_icon_image(self.app.state),
            title=self._status_text(),
            menu=self._menu(),
        )
        self._icon.run()
