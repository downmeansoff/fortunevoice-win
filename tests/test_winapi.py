"""The Windows edges.

Most of this module is thin ctypes wrappers where a test would only restate
the call. What is tested here is the part with decisions in it — the DPI
maths, the monitor fallback, the single-instance claim, and the key-state
mask — plus the small number of live calls whose contract is worth pinning
against a Windows that answers differently than expected.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

import pathlib

from fortunevoice import winapi  # noqa: E402

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"


# ── DPI ──────────────────────────────────────────────────────────────────


def test_the_scale_is_a_ratio_of_the_standard_dpi(monkeypatch):
    """96 dpi is 100%. Layout constants are multiplied by this, so an error
    here resizes every window in the app."""
    monkeypatch.setattr(winapi.user32, "GetDpiForSystem", lambda: 120)
    assert winapi.scale_factor() == pytest.approx(1.25)
    monkeypatch.setattr(winapi.user32, "GetDpiForSystem", lambda: 192)
    assert winapi.scale_factor() == pytest.approx(2.0)


def test_a_dpi_of_zero_falls_back_to_100_percent(monkeypatch):
    """Windows answers 0 for a window that has not been created yet. Dividing
    by that, or trusting it, would collapse the layout to nothing."""
    monkeypatch.setattr(winapi.user32, "GetDpiForSystem", lambda: 0)
    assert winapi.scale_factor() == 1.0


def test_an_old_windows_without_the_call_still_gets_a_scale(monkeypatch):
    """GetDpiForSystem is Windows 10 1607+. Older builds must not crash the
    import path of every window."""
    def missing():
        raise AttributeError("no GetDpiForSystem here")

    monkeypatch.setattr(winapi.user32, "GetDpiForSystem", missing)
    assert winapi.scale_factor() == 1.0


def test_the_real_display_reports_something_sane():
    """A live call. Any real display is between 100% and 400%; a number
    outside that means we are reading the wrong thing entirely."""
    assert 1.0 <= winapi.scale_factor() <= 4.0


# ── monitors ─────────────────────────────────────────────────────────────


def test_the_work_area_is_a_rectangle_with_area():
    """The overlay is placed inside this. An empty or inverted rectangle would
    put the pill off-screen, where the user simply never sees it again."""
    left, top, right, bottom = winapi.work_area_of_window(0)
    assert right > left and bottom > top


def test_the_work_area_is_read_not_the_monitor_rectangle(monkeypatch):
    """Placing the pill on the full monitor size is how it ended up underneath
    the taskbar. Asserting "not taller than the screen" is not enough — the
    monitor rectangle passes that too, which is exactly how this slipped
    through the first time. So both rectangles are filled with different
    values and the answer has to be the work one."""
    def fill(monitor, pointer):
        info = winapi.MONITORINFO.from_address(
            winapi.ctypes.addressof(pointer._obj))
        info.rcMonitor.left, info.rcMonitor.top = 0, 0
        info.rcMonitor.right, info.rcMonitor.bottom = 1920, 1080
        info.rcWork.left, info.rcWork.top = 0, 0
        info.rcWork.right, info.rcWork.bottom = 1920, 1032   # minus a taskbar
        return 1

    monkeypatch.setattr(winapi.user32, "MonitorFromWindow", lambda hwnd, flags: 7)
    monkeypatch.setattr(winapi.user32, "GetMonitorInfoW", fill)
    assert winapi.work_area_of_window(0) == (0, 0, 1920, 1032)


def test_the_real_work_area_is_no_larger_than_the_screen():
    """A live call, as a sanity check on the one above."""
    _, _, _, work_bottom = winapi.work_area_of_window(0)
    assert work_bottom <= winapi.user32.GetSystemMetrics(1)


def test_an_unknown_window_falls_back_to_a_usable_screen(monkeypatch):
    """No window to follow — the primary monitor is the only sane answer, and
    it must still be a rectangle rather than zeros."""
    monkeypatch.setattr(winapi.user32, "MonitorFromWindow", lambda hwnd, flags: 0)
    left, top, right, bottom = winapi.work_area_of_window(0)
    assert (left, top) == (0, 0)
    assert right > 0 and bottom > 0


# ── key state ────────────────────────────────────────────────────────────


def test_only_the_live_bit_counts(monkeypatch):
    """The low bit means "pressed since the last call" and is consumed by
    whoever reads it first — using it would race with anything else polling
    the same key, which in this app is the hotkey hook."""
    monkeypatch.setattr(winapi.user32, "GetAsyncKeyState", lambda vk: 0x0001)
    assert winapi.key_is_down(winapi.VK_ESCAPE) is False
    monkeypatch.setattr(winapi.user32, "GetAsyncKeyState", lambda vk: 0x8000)
    assert winapi.key_is_down(winapi.VK_ESCAPE) is True
    monkeypatch.setattr(winapi.user32, "GetAsyncKeyState", lambda vk: 0x8001)
    assert winapi.key_is_down(winapi.VK_ESCAPE) is True


def test_no_key_reads_as_down_when_nothing_is_pressed():
    """A live call: nothing is held during a test run, and a mask error would
    make Escape look permanently pressed — cancelling every dictation."""
    assert winapi.key_is_down(winapi.VK_ESCAPE) is False


# ── the foreground window ────────────────────────────────────────────────


def test_the_executable_wins_over_the_window_title(monkeypatch):
    """What per-app profiles are keyed by. Matching a profile against the
    title instead meant "WindowsTerminal.exe" never matched anything, so the
    whole feature was documented, configurable and dead."""
    monkeypatch.setattr(winapi, "process_name", lambda hwnd: "Telegram.exe")
    monkeypatch.setattr(winapi, "window_title", lambda hwnd: "Saved Messages")
    assert winapi.foreground_app_name() == "Telegram.exe"


def test_the_title_is_the_fallback_when_the_process_cannot_be_queried(monkeypatch):
    """A window we are not allowed to open still has to show as something."""
    monkeypatch.setattr(winapi, "process_name", lambda hwnd: None)
    monkeypatch.setattr(winapi, "window_title", lambda hwnd: "Notes - draft.txt")
    assert winapi.foreground_app_name() == "Notes - draft.txt"


def test_nothing_identifiable_becomes_none_not_an_empty_string(monkeypatch):
    """History stores this. An empty string would render as a blank chip on
    the card rather than no chip at all."""
    monkeypatch.setattr(winapi, "process_name", lambda hwnd: None)
    monkeypatch.setattr(winapi, "window_title", lambda hwnd: "")
    assert winapi.foreground_app_name() is None


def test_the_executable_of_this_very_process_is_readable():
    """The ctypes path itself — OpenProcess, QueryFullProcessImageNameW, the
    basename split. The tests above stub `process_name` out, so without this
    one a wrong argtype or a leaked handle would sit behind three green
    assertions. Asked of our own process, which we are always allowed to
    open."""
    import os

    name = winapi.process_name_of_pid(os.getpid())
    assert name and name.lower().endswith(".exe")


def test_the_title_of_a_window_that_does_not_exist_is_empty():
    assert winapi.window_title(0) == ""


# ── single instance ──────────────────────────────────────────────────────


def test_the_mutex_is_per_user_not_machine_wide():
    """Two Windows accounts on one machine each get their own tray app, their
    own hotkey and their own history. A Global\\ mutex would let the second
    user's app refuse to start."""
    assert winapi._MUTEX_NAME.startswith("Local")


class FakeCreateMutex:
    """Stands in for CreateMutexW, including the error code it leaves behind.

    The real one is declared with use_last_error, so the answer is read with
    `ctypes.get_last_error()` rather than from the return value — patching
    kernel32.GetLastError instead tests nothing.
    """

    restype = None
    argtypes = ()

    def __init__(self, error):
        self.error = error

    def __call__(self, *args):
        return 1234


def claim_with(monkeypatch, error):
    monkeypatch.setattr(winapi.kernel32, "CreateMutexW", FakeCreateMutex(error))
    monkeypatch.setattr(winapi.ctypes, "get_last_error", lambda: error)
    return winapi.claim_single_instance()


def test_a_free_name_is_claimed(monkeypatch):
    assert claim_with(monkeypatch, 0) is True


def test_a_second_instance_is_refused(monkeypatch):
    """Two instances would each install a keyboard hook, so one press starts
    two recordings and the transcript is typed twice into the document."""
    assert claim_with(monkeypatch, winapi.ERROR_ALREADY_EXISTS) is False


def test_any_other_error_still_lets_the_app_run(monkeypatch):
    """A locked-down profile that cannot create a mutex is not a reason to
    refuse to dictate."""
    assert claim_with(monkeypatch, 5) is True


def test_the_monitor_handle_is_not_truncated():
    """ctypes defaults an undeclared restype to C int — 32 bits, signed. An
    HMONITOR on 64-bit Windows is a pointer, so the handle came back truncated
    and GetMonitorInfoW was handed something that is not a monitor. It happened
    to work while the real handle fitted in 32 bits, and fell back to the
    primary screen when it did not — the overlay on the wrong display.

    Checked in a fresh process: monkeypatching a ctypes function pointer and
    restoring it loses the declaration, so any test that stubs
    MonitorFromWindow would make this pass or fail depending on the order.
    """
    import subprocess
    import sys

    code = (
        "import ctypes;"
        "from fortunevoice import winapi;"
        "r = winapi.user32.MonitorFromWindow.restype;"
        "print(ctypes.sizeof(r) == ctypes.sizeof(ctypes.c_void_p))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(SOURCE.parent))
    assert out.stdout.strip() == "True", (out.stdout, out.stderr)


def test_the_app_declares_its_own_identity():
    """Windows groups taskbar buttons, and picks their icon, by Application
    User Model ID. A Python app inherits pythonw.exe's — so the taskbar showed
    the Python logo beside our own window, and pinning it would have pinned
    Python."""
    assert winapi.APP_ID.startswith("FortuneVoice")
    winapi.set_app_id()  # must not raise


def test_setting_the_app_id_survives_a_refusal(monkeypatch):
    """It runs before logging is configured, so a failure here must be inert
    rather than a second exception on the startup path."""
    class Refuses:
        def __getattr__(self, name):
            raise OSError("no shell32 here")

    monkeypatch.setattr(winapi.ctypes, "windll", Refuses())
    winapi.set_app_id()
