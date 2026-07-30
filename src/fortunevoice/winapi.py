"""Shared Win32 ctypes declarations.

Only what the app actually calls. Everything here is plain ctypes rather than
pywin32: one less binary wheel to install, and no DLL-loading surprises when
the app is launched from a shortcut with a different working directory.

ULONG_PTR is declared explicitly per-architecture — getting it wrong silently
corrupts the INPUT struct layout on 64-bit, and SendInput then fails with no
error the caller can see.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - import guard for CI on Linux
    raise ImportError("fortunevoice.winapi requires Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# ── input synthesis ──────────────────────────────────────────────────────

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short

# ── window / focus ───────────────────────────────────────────────────────

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int

GUI_CARETBLINKING = 0x00000001


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetGUIThreadInfo.restype = wintypes.BOOL

# ── clipboard (legacy paste route only) ──────────────────────────────────

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalUnlock.restype = wintypes.BOOL


# ── window styles (the floating overlays) ────────────────────────────────

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

# GetWindowLongPtr only exists on 64-bit; the 32-bit build has to use the
# non-Ptr names, and calling the wrong one truncates the style word.
if ctypes.sizeof(ctypes.c_void_p) == 8:
    _get_long = user32.GetWindowLongPtrW
    _set_long = user32.SetWindowLongPtrW
    _get_long.restype = ctypes.c_longlong
    _set_long.restype = ctypes.c_longlong
    _get_long.argtypes = (wintypes.HWND, ctypes.c_int)
    _set_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_longlong)
else:  # pragma: no cover - 32-bit Python is not a supported target
    _get_long = user32.GetWindowLongW
    _set_long = user32.SetWindowLongW

user32.SetWindowPos.argtypes = (
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND


def toplevel_hwnd(window) -> int:
    """The real top-level HWND behind a Tk window.

    `winfo_id()` gives Tk's client window, which for a decorated toplevel is a
    child of a wrapper frame — setting styles on the child would do nothing
    visible. Tk exposes the frame through `wm_frame()`; fall back to walking up
    with GetParent for the borderless case where there is no wrapper.
    """
    try:
        return int(window.wm_frame(), 16)
    except Exception:  # noqa: BLE001 - not all Tk builds expose wm_frame
        hwnd = window.winfo_id()
        parent = user32.GetParent(hwnd)
        return int(parent or hwnd)


def make_non_activating(window) -> None:
    """Make a Tk window an overlay that never takes focus.

    This is the single most important detail in the whole overlay: FortuneVoice
    types into whatever window has focus. An overlay that activates when shown
    would become that window, and the dictation would be typed into a 96x30
    borderless panel instead of the user's editor.

    WS_EX_NOACTIVATE keeps it from being activated by a click or by being
    shown; WS_EX_TOOLWINDOW keeps it out of Alt-Tab and the taskbar.
    """
    hwnd = toplevel_hwnd(window)
    if not hwnd:
        return
    style = _get_long(hwnd, GWL_EXSTYLE)
    _set_long(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def make_click_through(window) -> None:
    """Let the mouse pass straight through an overlay.

    The pill floats over whatever the user is working in. Without this, a
    button underneath it silently stops responding for as long as a dictation
    lasts — a bug that would look like the *other* app being broken.

    WS_EX_TRANSPARENT only behaves on a layered window; Tk's
    `-transparentcolor` already sets WS_EX_LAYERED, and the pill relies on it
    for its rounded corners anyway.
    """
    hwnd = toplevel_hwnd(window)
    if not hwnd:
        return
    style = _get_long(hwnd, GWL_EXSTYLE)
    _set_long(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED)


def raise_without_focus(window) -> None:
    """Bring an overlay to the front without activating it.

    Tk's own `lift()` calls SetWindowPos without SWP_NOACTIVATE, so it steals
    focus from the app the user is dictating into.
    """
    hwnd = toplevel_hwnd(window)
    if not hwnd:
        return
    user32.SetWindowPos(
        hwnd, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def window_process_id(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def foreground_app_name() -> str | None:
    """Best-effort label for history ("which app did I dictate into").

    The window title is what the user recognises; the executable name would be
    more stable but means opening the process, which Defender's controlled
    folder access can refuse. A title is enough for a history row.
    """
    title = window_title(foreground_window())
    return title or None
