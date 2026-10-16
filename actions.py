"""System actions: volume, window switching, mouse control."""

from __future__ import annotations

import threading
import time
from typing import Any

from .contracts import ActionIntent


class ListeningFlag:
    """Thread-safe on/off switch shared by voice listener, app loop, and UI."""

    def __init__(self, initial: bool = True) -> None:
        self._event = threading.Event()
        if initial:
            self._event.set()

    def get(self) -> bool:
        """Return the current listening state."""
        return self._event.is_set()

    def set(self, value: bool) -> None:
        """Set the listening state."""
        if value:
            self._event.set()
        else:
            self._event.clear()


class SystemVolume:
    """Master volume control via pycaw; degrades gracefully off-Windows."""

    def __init__(self) -> None:
        self._endpoint = None
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            device = AudioUtilities.GetSpeakers()
            if hasattr(device, "EndpointVolume"):
                self._endpoint = device.EndpointVolume
            else:
                interface = device.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as exc:
            print(f"[volume] pycaw unavailable ({exc}) — volume actions disabled.")

    @property
    def available(self) -> bool:
        """Return True if volume control is available."""
        return self._endpoint is not None

    def adjust(self, percent_delta: int) -> int | None:
        """Change master volume by `percent_delta` and return the new level (0..100), or None."""
        if not self.available:
            return None
        current = self._endpoint.GetMasterVolumeLevelScalar()
        target = min(1.0, max(0.0, current + percent_delta / 100.0))
        self._endpoint.SetMasterVolumeLevelScalar(target, None)
        return int(round(target * 100))


class MouseControl:
    """Glue: hand observation -> gesture state machine -> virtual desktop -> OS.

    Responsibilities:
      * Feed FrameObservations into GestureStateMachine.
      * Apply dead zone, sensitivity, and smoothing to cursor position.
      * Map the resulting normalized position to the full virtual desktop
        (including negative monitor coordinates) via `display`.
      * Drive the OS mouse through MouseDriver, including safe release.
    """

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        from .display import get_virtual_screen
        from .gesture_state import GestureStateMachine
        from .mousedriver import MouseDriver

        settings = settings or {}
        self.control_enabled = bool(settings.get("control_enabled", False))
        self.input_error: str | None = None
        self._control_lock = threading.RLock()
        self.driver = MouseDriver()
        self._available = self.driver.available

        # Tunables (defaults; overridden by settings["mouse_*"]).
        self.smoothing = float(settings.get("mouse_smoothing", 0.35))
        self.dead_zone = float(settings.get("mouse_dead_zone", 0.05))
        self.sensitivity = float(settings.get("mouse_sensitivity", 1.0))
        self.pinch_threshold = float(settings.get("mouse_pinch_threshold", 0.40))
        self.middle_pinch_threshold = float(settings.get("mouse_middle_pinch_threshold", 0.40))
        self.ring_pinch_threshold = float(settings.get("mouse_ring_pinch_threshold", 0.40))
        self.enter_delay = float(settings.get("mouse_drag_enter_delay", 0.08))
        self.exit_delay = float(settings.get("mouse_drag_exit_delay", 0.08))
        self.click_cooldown = float(settings.get("mouse_click_cooldown", 0.35))
        self.scroll_sensitivity = float(settings.get("mouse_scroll_sensitivity", 1.0))
        self.scroll_dead_zone = float(settings.get("mouse_scroll_dead_zone", 0.03))

        # Gesture enable/disable.
        self.enable_drag = bool(settings.get("enable_drag", False))
        self.enable_left_click = bool(settings.get("enable_left_click", True))
        self.enable_right_click = bool(settings.get("enable_right_click", True))
        self.enable_scroll = bool(settings.get("enable_scroll", True))

        self.sm = GestureStateMachine(
            pinch_threshold=self.pinch_threshold,
            middle_pinch_threshold=self.middle_pinch_threshold,
            ring_pinch_threshold=self.ring_pinch_threshold,
            enter_delay=self.enter_delay,
            exit_delay=self.exit_delay,
            click_cooldown=self.click_cooldown,
            scroll_sensitivity=self.scroll_sensitivity,
            scroll_dead_zone=self.scroll_dead_zone,
        )
        self.sm.enable_drag = self.enable_drag
        self.sm.enable_left_click = self.enable_left_click
        self.sm.enable_right_click = self.enable_right_click
        self.sm.enable_scroll = self.enable_scroll

        # Cursor state.
        self._virtual = get_virtual_screen()
        self._smooth_pos: tuple[float, float] | None = None
        self._last_cursor: tuple[int, int] | None = None
        self._last_refresh = time.monotonic()
        self._last_action: str = ""  # current human-readable action label

    def set_control_enabled(self, enabled: bool) -> None:
        with self._control_lock:
            self._set_control_enabled_unlocked(enabled)

    def _set_control_enabled_unlocked(self, enabled: bool) -> None:
        """Switch preview/live modes without carrying held gestures across."""
        self.driver.release_all()
        self.sm.force_release()
        self._smooth_pos = None
        self._last_action = ""
        self.control_enabled = bool(enabled)
        if enabled:
            self.input_error = None

    def apply_settings(self, settings: dict[str, Any]) -> None:
        with self._control_lock:
            self._apply_settings_unlocked(settings)

    def _apply_settings_unlocked(self, settings: dict[str, Any]) -> None:
        """Apply safe mouse tuning immediately without replacing the driver."""
        self.driver.release_all()
        self.sm.force_release()
        self._smooth_pos = None
        for key, attr in (
            ("mouse_smoothing", "smoothing"),
            ("mouse_dead_zone", "dead_zone"),
            ("mouse_sensitivity", "sensitivity"),
            ("mouse_pinch_threshold", "pinch_threshold"),
            ("mouse_middle_pinch_threshold", "middle_pinch_threshold"),
            ("mouse_ring_pinch_threshold", "ring_pinch_threshold"),
            ("mouse_drag_enter_delay", "enter_delay"),
            ("mouse_drag_exit_delay", "exit_delay"),
            ("mouse_click_cooldown", "click_cooldown"),
            ("mouse_scroll_sensitivity", "scroll_sensitivity"),
            ("mouse_scroll_dead_zone", "scroll_dead_zone"),
        ):
            if key in settings:
                value = float(settings[key])
                setattr(self, attr, value)
                if hasattr(self.sm, attr):
                    setattr(self.sm, attr, value)
        for key in ("enable_drag", "enable_left_click", "enable_right_click", "enable_scroll"):
            if key in settings:
                value = bool(settings[key])
                setattr(self, key, value)
                setattr(self.sm, key, value)

    def _perform(self, operation: Any, *args: Any, **kwargs: Any) -> bool:
        """Latch an OS failure and return to safe preview mode."""
        if operation(*args, **kwargs):
            return True
        self.input_error = getattr(self.driver, "last_error", None) or "OS mouse input failed"
        self.set_control_enabled(False)
        print(f"[mouse] {self.input_error}; control paused")
        return False

    @property
    def available(self) -> bool:
        """Return True if mouse control is available on this platform."""
        return self._available

    def refresh_virtual_screen(self) -> None:
        """Re-query virtual desktop (monitors may have been added/removed)."""
        from .display import get_virtual_screen
        self._virtual = get_virtual_screen()

    def _apply_dead_zone_and_sensitivity(self, nx: float, ny: float) -> tuple[float, float]:
        """Map normalized (0..1) to a zoomed coordinate with center dead zone.

        Dead zone keeps the cursor still when the hand is near the frame
        center (small jitter). Sensitivity scales movement around center.
        """
        cx, cy = 0.5, 0.5
        dx = nx - cx
        dy = ny - cy

        dz = self.dead_zone
        if abs(dx) < dz:
            dx = 0.0
        if abs(dy) < dz:
            dy = 0.0

        # Expand the remaining range so the full 0..1 is still reachable.
        effective = 1.0 - 2.0 * dz
        if effective > 0:
            dx = (dx / effective) * self.sensitivity
            dy = (dy / effective) * self.sensitivity

        return cx + dx, cy + dy

    def _smooth(self, nx: float, ny: float) -> tuple[float, float]:
        """Exponential moving average of the target position."""
        if self._smooth_pos is None:
            self._smooth_pos = (nx, ny)
            return self._smooth_pos
        a = max(0.0, min(1.0, self.smoothing))
        sx = self._smooth_pos[0] + a * (nx - self._smooth_pos[0])
        sy = self._smooth_pos[1] + a * (ny - self._smooth_pos[1])
        self._smooth_pos = (sx, sy)
        return sx, sy

    def update(self, obs: Any, now: float | None = None, *, capture_ts: float = 0.0) -> dict[str, Any]:
        with self._control_lock:
            return self._update_unlocked(obs, now, capture_ts=capture_ts)

    def _update_unlocked(self, obs: Any, now: float | None = None, *,
                         capture_ts: float = 0.0) -> dict[str, Any]:
        """Advance one frame; returns the intent dict plus cursor position.

        capture_ts (engine-thread cap.read()-return timestamp) is forwarded to
        the mouse driver only when GC_MEASURE_LATENCY is set; otherwise it is
        a no-op default (0.0).
        """
        from .display import normalized_to_virtual

        if now is None:
            now = time.monotonic()

        if not self._available and getattr(self, "control_enabled", True):
            return {"state": "unavailable", "cursor": None, "input_error": "OS input unavailable"}

        # The engine publishes plain dicts; the state machine consumes
        # FrameObservation. Convert at the boundary so a dict can never
        # reach sm.update (which would raise AttributeError on .index_tip).
        if isinstance(obs, dict):
            from .gesture_state import FrameObservation
            obs = FrameObservation(
                thumb_tip=obs.get("thumb_tip"),
                index_tip=obs.get("index_tip"),
                middle_tip=obs.get("middle_tip"),
                ring_tip=obs.get("ring_tip"),
                hand_size=obs.get("hand_size"),
                thumb_index_dist=obs.get("thumb_index_dist"),
                thumb_middle_dist=obs.get("thumb_middle_dist"),
                thumb_ring_dist=obs.get("thumb_ring_dist"),
            )

        # Re-query virtual-screen bounds periodically to handle monitor
        # hot-plug/unplug without a restart (cheap GetSystemMetrics call).
        if now - self._last_refresh >= 2.0:
            self.refresh_virtual_screen()
            self._last_refresh = now

        intent = self.sm.update(obs, now=now)
        intent["typed_intent"] = ActionIntent(
            source="primary", action=intent["action"],
            armed=bool(getattr(self, "control_enabled", True)), timestamp=now,
        )
        if not intent["typed_intent"].armed:
            # Calibration computes real intents, but never touches the OS.
            self._last_action = intent.get("action", "")
            intent["cursor"] = None
            intent["simulation"] = True
            return intent
        intent["simulation"] = False

        target = intent.get("move")
        if target is not None:
            nx, ny = self._apply_dead_zone_and_sensitivity(target[0], target[1])
            sx, sy = self._smooth(nx, ny)
            # Clamp to [0,1] for mapping.
            sx = min(1.0, max(0.0, sx))
            sy = min(1.0, max(0.0, sy))
            x, y = normalized_to_virtual(sx, sy, self._virtual)
            if not self._perform(self.driver.move, x, y, capture_ts=capture_ts):
                intent.update(action="Input blocked", input_error=self.input_error, simulation=True)
                self._last_action = intent["action"]
                return intent
            self._last_cursor = (x, y)

        # Apply button transients, stopping immediately if Windows rejects one.
        for key, method in (
            ("left_down", self.driver.left_down),
            ("left_up", self.driver.left_up),
            ("left_click", self.driver.left_click),
            ("right_down", self.driver.right_down),
            ("right_up", self.driver.right_up),
            ("right_click", self.driver.right_click),
        ):
            if intent.get(key) and not self._perform(method):
                intent.update(action="Input blocked", input_error=self.input_error, simulation=True)
                self._last_action = intent["action"]
                return intent
        if intent.get("scroll") and not self._perform(self.driver.scroll, intent["scroll"]):
            intent.update(action="Input blocked", input_error=self.input_error, simulation=True)
            self._last_action = intent["action"]
            return intent

        intent["cursor"] = self._last_cursor
        self._last_action = intent.get("action", "")
        return intent

    def release(self) -> None:
        """Force-release all buttons (safety on loss/exit)."""
        with self._control_lock:
            self.sm.force_release()
            self.driver.release_all()
            self._last_action = ""

    def state(self) -> str:
        """Current state machine state string."""
        return self.sm.state.value

    def action(self) -> str:
        """Last human-readable action label."""
        return self._last_action


def switch_window(previous: bool) -> bool:
    """Alt+Tab (previous window) / Alt+Shift+Tab (next).

    Tries `keyboard` first (needs Admin for global hotkeys), falls back to `pywinauto`.
    """
    try:
        import keyboard
    except ImportError:
        pass
    else:
        try:
            keyboard.press("alt")
            time.sleep(0.06)
            keyboard.press_and_release("shift+tab" if previous else "tab")
            time.sleep(0.12)
            keyboard.release("alt")
            return True
        except Exception as exc:
            print(f"[windows] keyboard method failed: {exc}")

    try:
        import pywinauto
        from pywinauto import Desktop
    except ImportError:
        print("[windows] 'pywinauto' not installed — window switching disabled.")
        return False

    try:
        desktop = Desktop(backend="uia")
        fg = desktop.window(active_only=True)
        if fg.exists():
            fg.send_keys("%{TAB}" if not previous else "%+{TAB}")
            return True
    except Exception as exc:
        print(f"[windows] pywinauto method failed: {exc}")
    return False
