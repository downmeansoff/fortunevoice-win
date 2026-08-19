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


# ── single instance ──────────────────────────────────────────────────────

ERROR_ALREADY_EXISTS = 183
# Per-user, not global: two different Windows accounts on one machine each get
# their own tray app, their own hotkey and their own history, which is right.
_MUTEX_NAME = r"Local\FortuneVoice.SingleInstance"
_instance_mutex = None


def claim_single_instance() -> bool:
    """True when this process is the only FortuneVoice.

    Two instances is not a cosmetic problem: each installs its own low-level
    keyboard hook, so one press starts two recordings and the transcript is
    typed twice into the user's document. The handle is deliberately leaked
    for the life of the process — Windows releases it on exit.
    """
    global _instance_mutex

    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR)
    _instance_mutex = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


# ── DPI ──────────────────────────────────────────────────────────────────

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)


def set_dpi_awareness() -> None:
    """Ask Windows to stop bitmap-stretching this process.

    Without it a 150% display renders the whole UI at 100% and scales the
    result up: blurry text, blurry icons, and a recording pill that lands in
    the wrong place because the coordinates it reports are virtualised.

    Must run before any window exists. Per-monitor v2 first (Windows 10 1703+),
    system-aware as the fallback; both can legitimately fail if the process
    manifest already set awareness, which is fine.
    """
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        if user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_SYSTEM_AWARE):
            return
    except AttributeError:
        pass
    try:  # Windows 8.1
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - nothing left to try
        pass


def scale_factor(hwnd: int = 0) -> float:
    """Display scale for a window (1.0 = 100%, 1.5 = 150%).

    Tk sizes widgets in raw pixels, so a DPI-aware process draws a 40 px row
    as 40 physical pixels — correct on 100%, half the intended size on 200%.
    Layout constants are multiplied by this.
    """
    try:
        user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd)) if hwnd else 0
    except AttributeError:
        dpi = 0
    if not dpi:
        try:
            dpi = user32.GetDpiForSystem()
        except AttributeError:
            dpi = 96
    return (dpi or 96) / 96.0


# ── monitors ─────────────────────────────────────────────────────────────

MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


# Declared, because ctypes defaults an undeclared restype to C int — 32 bits,
# signed. An HMONITOR on 64-bit Windows is a pointer, so the handle came back
# truncated and GetMonitorInfoW was handed something that is not a monitor. It
# happened to work while the real handle fitted in 32 bits, and fell back to
# the primary screen when it did not — the overlay on the wrong display.
user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
user32.GetMonitorInfoW.restype = wintypes.BOOL


def work_area_of_window(hwnd: int) -> tuple[int, int, int, int]:
    """Work area (screen minus taskbar) of the monitor holding `hwnd`.

    The overlay used to be placed on the primary monitor's screen size, which
    on a two-monitor desk put it on the wrong screen entirely — and even on
    one screen, ignoring the work area meant it could sit under the taskbar.
    Falls back to the primary monitor when there is no window to follow.
    """
    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


VK_ESCAPE = 0x1B


def key_is_down(vk: int) -> bool:
    """Is this key physically down right now?

    GetAsyncKeyState's high bit is the live state; the low bit ("pressed since
    last call") is deliberately ignored, because it is consumed by whoever
    reads it first and would race with anything else polling the same key.
    """
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ── window chrome ────────────────────────────────────────────────────────

# Windows 10 1809 used 19; 1903+ and Windows 11 use 20. Setting both is
# harmless — the build that doesn't know an attribute just returns an error.
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19


def use_dark_titlebar(window) -> bool:
    """Paint the title bar dark to match the window below it.

    Tk styles the client area only, so a dark app gets a white Windows title
    bar sitting on top of it — the single most "unfinished" thing about the
    UI, and visible on every window at once. This is the documented way to fix
    it; there is no Tk option for it.
    """
    hwnd = toplevel_hwnd(window)
    if not hwnd:
        return False
    try:
        dwm = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover - Windows without DWM
        return False
    value = ctypes.c_int(1)
    for attribute in (DWMWA_USE_IMMERSIVE_DARK_MODE,
                      DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        if dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attribute),
            ctypes.byref(value), ctypes.sizeof(value),
        ) == 0:
            return True
    return False
