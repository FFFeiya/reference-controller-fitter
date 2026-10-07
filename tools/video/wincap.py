"""Capture a single window even when it is behind other windows (Windows only).

Uses PrintWindow(PW_RENDERFULLCONTENT), which asks DWM for the window's own content, so
nothing else on the desktop is recorded. Also helpers to find a window by title and to
park it behind all other windows without stealing focus.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # per-monitor v2: real pixels
except Exception:
    pass

PW_RENDERFULLCONTENT = 0x00000002
HWND_BOTTOM = 1
SWP_NOACTIVATE, SWP_NOZORDER, SWP_SHOWWINDOW = 0x0010, 0x0004, 0x0040
SW_RESTORE, SW_SHOWNOACTIVATE = 9, 4

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_windows(title_part: str, pid: int | None = None) -> list[tuple[int, str]]:
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_part.lower() in buf.value.lower():
            if pid is not None:
                p = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                if p.value != pid:
                    return True
            out.append((hwnd, buf.value))
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def wait_window(title_part: str, timeout: float = 300, pid: int | None = None) -> int:
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = find_windows(title_part, pid)
        if w:
            return w[0][0]
        time.sleep(0.5)
    raise TimeoutError(f"window '{title_part}' not found")


def park_behind(hwnd: int, x: int = 0, y: int = 0, w: int = 1920, h: int = 1080) -> None:
    """Restore (if minimized), size it, and put it behind every other window without activating."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetWindowPos(hwnd, HWND_BOTTOM, x, y, w, h, SWP_NOACTIVATE)


def client_size(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def grab(hwnd: int) -> np.ndarray | None:
    """BGR image of the whole window (including title bar)."""
    w, h = client_size(hwnd)
    if w <= 0 or h <= 0:
        return None
    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)
    if not ok:
        return None
    img = np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, :3].copy()
    return img
