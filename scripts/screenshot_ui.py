"""Screenshot every page of the window, with plausible data.

Looking at the UI is the only way to review it, and doing that by hand means
opening the app, dictating a few times to get a history worth looking at, and
remembering to visit all four pages. This does it in one command, against a
throwaway data folder, so nothing here touches what the user has dictated:

    python scripts/screenshot_ui.py out/

Writes 01-history.png … 04-settings.png into the directory given.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "ui-shots")
OUT.mkdir(parents=True, exist_ok=True)
# Before importing anything from the package: paths reads this at first use.
os.environ["FORTUNEVOICE_HOME"] = tempfile.mkdtemp(prefix="fv-shots-")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fortunevoice import log, metrics, winapi  # noqa: E402

log.setup(debug=False)
winapi.set_dpi_awareness()

from fortunevoice.store import DictationRecord, DictationStore  # noqa: E402

SAMPLES = [
    ("Code.exe", "Проверь, пожалуйста, последний коммит — там поменялась логика подписки."),
    ("Telegram.exe", "Привет! Сегодня встречаемся в семь, я забронировал столик на четверых."),
    ("chrome.exe", "Напомни мне купить билеты на поезд до Самары на пятницу."),
    ("Code.exe", "Добавь тест на то, что настройка переживает перезапуск приложения."),
    ("explorer.exe", "Новая строка нужна здесь, а дальше по списку."),
    ("Telegram.exe", "Да, всё в силе, до встречи."),
]

store = DictationStore()
for index, (app_name, text) in enumerate(SAMPLES):
    store.add(DictationRecord(
        date=metrics.now(), words=len(text.split()),
        duration=2.5 + index * 0.7, app=app_name, transcript=text,
    ))
    metrics.record(metrics.DictationMetric(
        date=metrics.now(), capture_sec=2.5 + index * 0.7, stt_ms=380.0 + index * 20,
        cleanup_ms=520.0, total_ms=980.0 + index * 30, chars=len(text),
        outcome="typed", cleanup_skipped=False, retried=False, device="cuda",
        stream_passes=2, logprob=-0.21, model="large-v3-turbo",
    ))

from fortunevoice.ui import main_window, ui  # noqa: E402

PAGES = [("History", "01-history"), ("Insights", "02-insights"),
         ("Dictionary", "03-dictionary"), ("Settings", "04-settings")]


def capture(window, name: str) -> None:
    """Ask the window to draw itself into a bitmap.

    Not a screen grab: this process cannot take the foreground — Windows
    refuses it to a process that was not the last to receive input — so
    grabbing the screen region captured whatever happened to be in front.
    PrintWindow asks the window itself, and works while it is behind others.
    """
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    window.update_idletasks()
    window.update()
    time.sleep(0.35)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    # Declared, all of them. ctypes passes an undeclared argument as C int, so
    # a 64-bit DC or bitmap handle is truncated on the way in — which is the
    # same defect this project has now fixed twice in its own Win32 code.
    user32.GetWindowDC.argtypes = (wintypes.HWND,)
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.PrintWindow.argtypes = (wintypes.HWND, wintypes.HDC, wintypes.UINT)
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)

    hwnd = int(window.frame(), 16) if window.frame().startswith("0x") else int(window.frame())
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top

    source = user32.GetWindowDC(wintypes.HWND(hwnd))
    memory = gdi32.CreateCompatibleDC(source)
    bitmap = gdi32.CreateCompatibleBitmap(source, width, height)
    gdi32.SelectObject(memory, bitmap)
    # 2 = PW_RENDERFULLCONTENT, which is what makes it work for a window that
    # is not in front and for one drawn with hardware acceleration.
    user32.PrintWindow(wintypes.HWND(hwnd), memory, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.UINT)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth, header.biHeight = width, -height   # negative = top-down
    header.biPlanes, header.biBitCount = 1, 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)

    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(wintypes.HWND(hwnd), source)

    path = OUT / f"{name}.png"
    image.convert("RGB").save(path)
    print(f"  {path.name}  {image.width}x{image.height}")


def run() -> None:
    window = main_window.window
    window._store = store  # noqa: SLF001 - a harness, not the app
    window._build()  # noqa: SLF001
    top = window._window  # noqa: SLF001
    top.deiconify()
    top.lift()
    top.geometry("1060x720+60+60")
    for page, name in PAGES:
        window._select(page)  # noqa: SLF001
        top.update_idletasks()
        top.update()
        time.sleep(0.4)
        capture(top, name)
    print("done")
    os._exit(0)


ui.on_error = lambda detail: print("UI ERROR:", detail)
if not ui.start():
    print("the UI thread refused to start")
    os._exit(2)
time.sleep(1.0)
ui.call(run)
time.sleep(60)
print("timed out")
os._exit(1)
