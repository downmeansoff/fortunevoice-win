"""Inserts text into the focused app by typing it as synthesized key events.

Port of Sources/FortuneVoice/TextInjector.swift, keeping its central decision:
**the clipboard is not involved.** Routing every dictation through the
clipboard and relying on a timer to put the old contents back meant a
dictation that outlived the timer just stayed there, and the clipboard was
clobbered on every use. Typing the characters leaves it untouched.

Windows specifics the macOS version didn't have to deal with:

* **Held modifiers.** The hotkey is a chord (default Ctrl+Alt+Space) and users
  release the trigger key first, so Ctrl and Alt are usually still physically
  down when the text lands. macOS could just clear the flags on its synthetic
  events; Windows keeps global modifier state, so every typed character would
  arrive as a shortcut. We synthesize key-ups for whatever is held before
  typing.
* **Newlines.** KEYEVENTF_UNICODE with U+000A does nothing in most apps — the
  cleanup model emits "- " bullets on separate lines, so newlines are sent as
  a real VK_RETURN.
* **Surrogate pairs.** SendInput takes UTF-16 code units. Anything outside the
  BMP (emoji) must go as two consecutive events, never one.
"""

from __future__ import annotations

import ctypes
import time

from . import config, profiles
from .log import get as get_logger
from . import winapi
from .winapi import (
    CF_UNICODETEXT,
    GMEM_MOVEABLE,
    GUI_CARETBLINKING,
    GUITHREADINFO,
    INPUT,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK_CONTROL,
    VK_LWIN,
    VK_MENU,
    VK_RETURN,
    VK_RWIN,
    VK_SHIFT,
    VK_V,
    kernel32,
    user32,
    wintypes,
)

logger = get_logger("injector")

# Code units per SendInput batch. Events carry a unicode payload rather than a
# keycode, so this is only about not handing the kernel one enormous array.
_TYPE_CHUNK = 20
# Breather between batches. Without it, apps that process keystrokes
# asynchronously (terminals, Electron) drop characters under a fast burst.
_TYPE_CHUNK_DELAY = 0.0012

_MODIFIERS = (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN)


def _key_event(vk: int, scan: int, flags: int) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki.wVk = vk
    event.ki.wScan = scan
    event.ki.dwFlags = flags
    event.ki.time = 0
    event.ki.dwExtraInfo = 0
    return event


def _send(events: list[INPUT]) -> bool:
    if not events:
        return True
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        logger.error(
            "SendInput sent %d of %d events (error %d)",
            sent, len(events), ctypes.get_last_error(),
        )
        return False
    return True


def release_held_modifiers() -> None:
    """Tell Windows the modifier keys are up before we type.

    Whatever is still physically held stays held from the user's point of
    view; when they let go, the real key-up is simply redundant. The
    alternative — typing "привет" while Ctrl is down — fires six shortcuts in
    the target app.
    """
    events = []
    for vk in _MODIFIERS:
        if user32.GetAsyncKeyState(vk) & 0x8000:
            events.append(_key_event(vk, 0, KEYEVENTF_KEYUP))
    if events:
        _send(events)
        time.sleep(0.005)  # let the target app process the state change


def type_text(text: str) -> bool:
    """Type `text` into whatever has focus. Returns False if SendInput failed."""
    if not text:
        return True
    release_held_modifiers()

    events: list[INPUT] = []
    ok = True

    def flush() -> None:
        nonlocal events, ok
        if not events:
            return
        if not _send(events):
            ok = False
        events = []
        time.sleep(_TYPE_CHUNK_DELAY)

    for char in text:
        if char == "\r":
            continue  # CRLF would produce two line breaks
        if char == "\n":
            flush()
            if not _send([_key_event(VK_RETURN, 0, 0), _key_event(VK_RETURN, 0, KEYEVENTF_KEYUP)]):
                ok = False
            time.sleep(_TYPE_CHUNK_DELAY)
            continue
        for code_unit in _utf16_units(char):
            events.append(_key_event(0, code_unit, KEYEVENTF_UNICODE))
            events.append(_key_event(0, code_unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        if len(events) >= _TYPE_CHUNK * 2:
            flush()
    flush()
    return ok


def _utf16_units(char: str) -> list[int]:
    """UTF-16 code units for one character — two for anything above the BMP."""
    encoded = char.encode("utf-16-le")
    return [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]


# Consoles that do not take Ctrl+V. Windows terminals bind paste to
# Ctrl+Shift+V, or to right-click, or to nothing at all — and a paste they
# ignore is a dictation that goes nowhere, which is the one failure this app
# is built never to produce. Typing is slower and always works.
#
# Matched on the executable, lowercased, so "WindowsTerminal.exe" and
# "windowsterminal.exe" are the same thing.
_CONSOLES = frozenset({
    "windowsterminal.exe", "wt.exe", "conhost.exe", "cmd.exe",
    "powershell.exe", "pwsh.exe", "mintty.exe", "bash.exe",
    "alacritty.exe", "wezterm-gui.exe", "kitty.exe", "putty.exe",
    "conemu.exe", "conemu64.exe", "hyper.exe", "tabby.exe",
    "far.exe", "cmder.exe",
})


def is_console(app: str | None) -> bool:
    return bool(app) and app.lower() in _CONSOLES


def wants_paste(text: str, app: str | None) -> bool:
    """Should this text go through the clipboard rather than the keyboard?

    Typing costs one keystroke per character and the receiving app pays it:
    measured into a bare Tk text field, 732 characters take 1.9 s to arrive,
    and an app that runs handlers per keystroke is far slower still. A paste
    is one event whatever the length — so short text is typed, which leaves
    the clipboard untouched, and long text is pasted, which is the difference
    between instant and watching it spell itself out.
    """
    if profiles.get_bool("FVPasteViaClipboard", app):
        return True                      # the old boolean still wins
    mode = (profiles.get_str("FVDelivery", app) or "auto").lower()
    if mode == "paste":
        return True
    if mode == "type":
        return False
    # "auto" only. An explicit "paste" above still wins, because a user who
    # asked for it in a terminal knows something we do not.
    if is_console(app):
        return False
    return len(text) > max(1, config.get_int("FVPasteOver"))


def inject(text: str) -> bool:
    """Write `text` into the focused app. False when the write failed and the
    caller should fall back to the result panel."""
    if not text:
        return True
    # Resolved here rather than by the caller: this is the last moment the
    # window in front is still the one the text is going into, and the setting
    # is per-application (profiles.OVERRIDABLE) precisely because the apps that
    # want one route or the other are specific ones.
    if wants_paste(text, winapi.foreground_app_name()):
        return paste_via_clipboard(text)
    return type_text(text)


# ── legacy clipboard route, behind FVPasteViaClipboard ───────────────────


def paste_via_clipboard(text: str) -> bool:
    """Escape hatch for apps that ignore synthesized unicode input (some Java
    and older Electron ones). Restores the previous contents afterwards, but
    only if nothing else wrote to the clipboard meanwhile — which is exactly
    why it is no longer the default: that condition often does not hold, and
    the dictation is then left sitting in the user's clipboard."""
    previous = _clipboard_text()
    if not set_clipboard_text(text):
        return False
    sequence = user32.GetClipboardSequenceNumber()

    release_held_modifiers()
    ok = _send(
        [
            _key_event(VK_CONTROL, 0, 0),
            _key_event(VK_V, 0, 0),
            _key_event(VK_V, 0, KEYEVENTF_KEYUP),
            _key_event(VK_CONTROL, 0, KEYEVENTF_KEYUP),
        ]
    )

    # 5 s, not 1.2: right after a dictation the machine is still busy and a
    # loaded target app can service Ctrl+V later than 1.2 s — restoring first
    # made the paste land the OLD clipboard and the transcript "vanish".
    def restore() -> None:
        time.sleep(5.0)
        if user32.GetClipboardSequenceNumber() != sequence:
            return  # someone else wrote to the clipboard; leave it alone
        if previous is not None:
            set_clipboard_text(previous)
            return
        # Nothing to put back — the clipboard was empty, or held an image or
        # files, which this code cannot reproduce. Leaving it as it is means
        # leaving the dictation there: every Ctrl+V for the rest of the day,
        # and every clipboard-history tool, holding something the user said
        # out loud. Clearing loses nothing that was recoverable anyway.
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
            finally:
                user32.CloseClipboard()

    import threading

    threading.Thread(target=restore, name="clipboard-restore", daemon=True).start()
    return ok


def _clipboard_text() -> str | None:
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not user32.OpenClipboard(None):
        logger.warning("could not open the clipboard")
        return False
    handle = None
    try:
        buffer = (text + chr(0)).encode("utf-16-le")
        # Allocated and filled BEFORE emptying. The other order destroyed the
        # user's clipboard and then returned False if the allocation or the
        # lock failed — they lost whatever they had copied, in exchange for
        # nothing at all.
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buffer))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return False
        ctypes.memmove(pointer, buffer, len(buffer))
        kernel32.GlobalUnlock(handle)

        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        # Ownership has passed to the clipboard: freeing it now would hand the
        # next reader a dangling block.
        handle = None
        return True
    finally:
        if handle:
            kernel32.GlobalFree(handle)  # it never reached the clipboard
        user32.CloseClipboard()


# ── focus inspection ─────────────────────────────────────────────────────


def focused_element_is_editable() -> bool | None:
    """Is the focused UI element a text input?

    True = there is a caret, so definitely editable. False = there is no
    foreground window at all, so there is nowhere to type. None = can't tell.

    macOS could ask the Accessibility API for a role; the equivalent on
    Windows is UI Automation, which costs a COM round-trip per dictation and
    still can't classify Electron or custom-drawn editors. So this stays
    deliberately weak and biased toward pasting: the macOS build already
    treats "can't tell" as "paste anyway", because losing a dictation is worse
    than an occasional unwanted paste.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        logger.info("focus check — no foreground window")
        return False

    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(wintypes.DWORD(thread_id), ctypes.byref(info)):
        return None
    if info.hwndCaret or (info.flags & GUI_CARETBLINKING):
        return True
    return None
