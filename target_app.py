"""Foreground-bound, allowlisted shortcuts for paired-hand gestures."""
from __future__ import annotations

import ctypes
import ntpath
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

# No user-provided key sequences. Unsupported action/profile pairs never dispatch.
SHORTCUT_PROFILES = {
    "chrome.exe": {"tab_next": "ctrl+tab", "tab_previous": "ctrl+shift+tab",
                   "zoom_in": "ctrl+equal", "zoom_out": "ctrl+minus"},
    "msedge.exe": {"tab_next": "ctrl+tab", "tab_previous": "ctrl+shift+tab",
                   "zoom_in": "ctrl+equal", "zoom_out": "ctrl+minus"},
    "firefox.exe": {"tab_next": "ctrl+tab", "tab_previous": "ctrl+shift+tab",
                    "zoom_in": "ctrl+equal", "zoom_out": "ctrl+minus"},
    "code.exe": {"tab_next": "ctrl+page down", "tab_previous": "ctrl+page up",
                 "zoom_in": "ctrl+equal", "zoom_out": "ctrl+minus"},
    "winword.exe": {"zoom_in": "ctrl+plus", "zoom_out": "ctrl+minus"},
    "notepad.exe": {"tab_next": "ctrl+tab", "tab_previous": "ctrl+shift+tab",
                    "zoom_in": "ctrl+plus", "zoom_out": "ctrl+minus"},
}


@dataclass(frozen=True)
class ForegroundWindow:
    hwnd: int
    executable: str
    title: str


def _read_windows_foreground() -> ForegroundWindow | None:
    """Read the real foreground HWND, owning process image, and visible title.

    A missing/failed WinAPI result is not an identity; it must never be bound.
    """
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return None
        process = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not process:
            return None
        try:
            image = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(image))
            ok = kernel32.QueryFullProcessImageNameW(process, 0, image, ctypes.byref(size))
        finally:
            closed = kernel32.CloseHandle(process)
        if not ok or not closed or not size.value or not image.value:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        if user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer)) <= 0:
            return None
        return ForegroundWindow(int(hwnd), ntpath.basename(image.value), title_buffer.value)
    except (OSError, AttributeError, TypeError, ValueError):
        return None


def _send_keyboard_chord(chord: str) -> None:
    """Only called with a constant chord after the foreground recheck."""
    import keyboard
    keyboard.send(chord)


class FocusGuard:
    """Require an explicit bind to a supported foreground window."""

    def __init__(self, foreground_reader: Callable[[], ForegroundWindow | None] | None = None) -> None:
        self._reader = foreground_reader if foreground_reader is not None else _read_windows_foreground
        self._bound: ForegroundWindow | None = None

    def clear(self) -> None:
        self._bound = None

    def bind_foreground(self) -> bool:
        self.clear()
        if sys.platform != "win32" or self._reader is None:
            return False
        try:
            window = self._reader()
        except Exception:
            return False
        if not isinstance(window, ForegroundWindow) or not self._valid(window):
            return False
        self._bound = window
        return True

    @staticmethod
    def _valid(window: ForegroundWindow) -> bool:
        return (type(window.hwnd) is int and window.hwnd > 0
                and isinstance(window.executable, str)
                and window.executable.lower() in SHORTCUT_PROFILES
                and isinstance(window.title, str)
                and not any(token in window.title.casefold() for token in ("navi", "preview")))

    def focus_ok(self) -> bool:
        if self._bound is None or sys.platform != "win32":
            return False
        try:
            current = self._reader()
        except Exception:
            self.clear()
            return False
        if (not isinstance(current, ForegroundWindow) or not self._valid(current)
                or current.hwnd != self._bound.hwnd
                or current.executable.lower() != self._bound.executable.lower()):
            self.clear()
            return False
        return True


class ShortcutExecutor:
    """Send only supported action shortcuts after a fresh focus check."""

    def __init__(self, keyboard_sender: Callable[[str], object] | None = None) -> None:
        self._send = keyboard_sender if keyboard_sender is not None else _send_keyboard_chord

    def execute(self, action: str, *, guard: FocusGuard, armed: bool) -> bool:
        if armed is not True or not isinstance(guard, FocusGuard) or not guard.focus_ok():
            return False
        profile = SHORTCUT_PROFILES[guard._bound.executable.lower()]
        chord = profile.get(action) if isinstance(action, str) else None
        if chord is None or self._send is None:
            return False
        try:
            self._send(chord)
            return True
        except Exception:
            guard.clear()
            return False
