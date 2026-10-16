"""Low-level Windows mouse driver via ctypes (SendInput / SetCursorPos).

This module is the platform boundary: everything else (state machine, display
mapping, smoothing) is pure and testable, while this module talks to the OS.
Uses SendInput for button up/down and wheel, and SetCursorPos for absolute
cursor placement (which natively handles the full virtual desktop including
negative coordinates and DPI scaling, because the OS maps physical pixel
coordinates itself).
"""

from __future__ import annotations

import ctypes
import os
import time
from typing import Any

# ---------- latency measurement (opt-in via GC_MEASURE_LATENCY) ----------
# One entry per SetCursorPos call while measuring:
#   {"capture_ts": float, "driver_ts": float, "x": int, "y": int}
# capture_ts = engine thread's cap.read()-return timestamp for the frame that
# drove this move; driver_ts = SetCursorPos call time (main thread).
_DRIVER_MOVE_HOOKS: list[dict[str, Any]] = []


def clear_driver_move_hooks() -> None:
    """Empty the recorded end-to-end move hooks (call at measurement-window start)."""
    _DRIVER_MOVE_HOOKS.clear()


def get_driver_move_hooks() -> list[dict[str, Any]]:
    """Return the recorded end-to-end move hooks."""
    return list(_DRIVER_MOVE_HOOKS)


# Win32 constants
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_ABSOLUTE = 0x8000

_WHEEL_DELTA = 120

try:
    # Preserve GetLastError for meaningful diagnostics after a failed call.
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
except Exception:
    _user32 = None


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


def _send_input(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> bool:
    """Inject a low-level mouse event via SendInput."""
    if _user32 is None:
        return False
    inp = _INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.mouseData = mouse_data
    inp.union.mi.dwFlags = flags
    inp.union.mi.time = 0
    inp.union.mi.dwExtraInfo = None
    try:
        return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp)) == 1
    except Exception:
        return False


class MouseDriver:
    """Actual OS cursor/button control."""

    def __init__(self) -> None:
        self.available = _user32 is not None
        self._left_down = False
        self._right_down = False
        self.last_error: str | None = None

    def _send(self, flags: int, *, mouse_data: int = 0) -> bool:
        if _send_input(flags, mouse_data=mouse_data):
            self.last_error = None
            return True
        code = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
        self.last_error = (f"Windows blocked mouse input (error {code})" if code
                           else "Windows blocked mouse input (SendInput returned 0)")
        return False

    def move(self, x: int, y: int, *, capture_ts: float = 0.0) -> bool:
        """Move the cursor to absolute virtual-desktop coordinates."""
        if not self.available:
            return False
        driver_ts = time.monotonic()
        if os.environ.get("GC_MEASURE_LATENCY") and capture_ts:
            _DRIVER_MOVE_HOOKS.append({
                "capture_ts": capture_ts,
                "driver_ts": driver_ts,
                "x": int(x),
                "y": int(y),
            })
        try:
            ok = bool(_user32.SetCursorPos(int(x), int(y)))
        except Exception as exc:
            self.last_error = f"Cursor move failed: {exc}"
            return False
        if ok:
            self.last_error = None
        else:
            self.last_error = "Windows rejected cursor movement"
        return ok

    def left_down(self) -> bool:
        """Press the left mouse button."""
        if not self.available:
            return False
        if self._send(_MOUSEEVENTF_LEFTDOWN):
            self._left_down = True
            return True
        return False

    def left_up(self) -> bool:
        """Release the left mouse button."""
        if not self.available or not self._left_down:
            return False
        if not self._send(_MOUSEEVENTF_LEFTUP):
            return False
        self._left_down = False
        return True

    def left_click(self) -> bool:
        """Press and release the left mouse button."""
        if not self.available:
            return False
        if not self.left_down():
            return False
        time.sleep(0.005)
        return self.left_up()

    def right_down(self) -> bool:
        """Press the right mouse button."""
        if not self.available:
            return False
        if self._send(_MOUSEEVENTF_RIGHTDOWN):
            self._right_down = True
            return True
        return False

    def right_up(self) -> bool:
        """Release the right mouse button."""
        if not self.available or not self._right_down:
            return False
        if not self._send(_MOUSEEVENTF_RIGHTUP):
            return False
        self._right_down = False
        return True

    def right_click(self) -> bool:
        """Press and release the right mouse button."""
        if not self.available:
            return False
        if not self.right_down():
            return False
        time.sleep(0.005)
        return self.right_up()

    def scroll(self, clicks: int) -> bool:
        """Scroll the mouse wheel by `clicks` wheel notches."""
        if not self.available:
            return False
        return self._send(_MOUSEEVENTF_WHEEL, mouse_data=clicks * _WHEEL_DELTA)

    def release_all(self) -> None:
        """Guarantee no button is left held (called on shutdown/loss).

        Only releases buttons that are actually down to avoid spurious events.
        """
        if self._left_down:
            self.left_up()
        if self._right_down:
            self.right_up()

    @property
    def left_held(self) -> bool:
        """True when the left button is currently held."""
        return self._left_down

    @property
    def right_held(self) -> bool:
        """True when the right button is currently held."""
        return self._right_down
