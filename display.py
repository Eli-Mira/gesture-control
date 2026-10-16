"""Multi-monitor / virtual-desktop coordinate mapping for Windows.

Maps the normalized hand/camera coordinate space ([0..1] x [0..1], where
(0,0) is the *primary* monitor's top-left and (1,1) is its bottom-right, per
MediaPipe's normalized landmarks) onto the full Windows virtual desktop.

The virtual desktop spans all connected displays and can include negative
offsets (e.g. a monitor to the left/top of the primary). The default
pyautogui.size() only reports the primary monitor, which is why cursor
control previously clamped to a single screen. This module instead queries
the OS for the true virtual-screen bounds via GetSystemMetrics.

Note on the coordinate source: `nx`/`ny` (the hand position) arrive already
normalized to the *camera frame* (0..1). We deliberately do NOT assume the
camera is only "one monitor" — the whole virtual desktop is the target. The
mapping is intentionally configurable via an input "camera region" concept:
sensitivity/offset are applied in the caller (MouseControl), while this
module is responsible purely for correct absolute-position math across the
virtual desktop.
"""

from __future__ import annotations

import ctypes
import os
from typing import Any


def _get_user32() -> Any | None:
    """Return user32 DLL binding, or None on non-Windows."""
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def get_virtual_screen() -> dict[str, int]:
    """Return virtual-desktop bounds in physical pixels.

    Returns {"left": int, "top": int, "width": int, "height": int}.
    Uses SM_[X/Y/CX/CY]VIRTUALSCREEN; these are physical-pixel values that
    already incorporate per-monitor DPI scaling from the OS's perspective,
    so no manual DPI multiplication is required here.
    """
    user32 = _get_user32()
    if user32 is None:
        # Non-Windows fallback: pretend a single 1920x1080 screen at origin.
        return {"left": 0, "top": 0, "width": 1920, "height": 1080}

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    left = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))

    # Guard against pathological zero values (e.g. during logon screen).
    if width <= 0 or height <= 0:
        return {"left": 0, "top": 0, "width": 1920, "height": 1080}

    return {"left": left, "top": top, "width": width, "height": height}


def normalized_to_virtual(nx: float, ny: float, screen: dict[str, int] | None = None) -> tuple[int, int]:
    """Map normalized (0..1, 0..1) coords to absolute virtual-desktop pixels.

    (0,0) -> top-left of virtual desktop (including negative offsets).
    (1,1) -> bottom-right of virtual desktop.
    """
    if screen is None:
        screen = get_virtual_screen()
    left = screen["left"]
    top = screen["top"]
    width = screen["width"]
    height = screen["height"]

    # Clamp to a sane range in case of malformed input.
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))

    x = left + int(round(nx * (width - 1)))
    y = top + int(round(ny * (height - 1)))
    return x, y


def set_cursor_pos(x: int, y: int) -> bool:
    """Move the cursor to absolute virtual-desktop coordinates via SetCursorPos.

    Returns True on success. SetCursorPos accepts full virtual-desktop
    coordinates (including negatives) natively on Windows.
    """
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.SetCursorPos(int(x), int(y))
        return True
    except Exception:
        return False


def get_cursor_pos() -> tuple[int, int] | None:
    """Return the current cursor position in virtual-desktop coordinates."""
    user32 = _get_user32()
    if user32 is None:
        return None

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    try:
        if user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    screen = get_virtual_screen()
    print("Virtual desktop:", screen)
    for nx, ny in [(0, 0), (0.5, 0.5), (1, 1)]:
        print(f"({nx},{ny}) -> {normalized_to_virtual(nx, ny, screen)}")
    print("Cursor pos:", get_cursor_pos())
