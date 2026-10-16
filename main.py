"""Main entry point for gesture control app.

Supports both:
    python main.py              # direct execution
    python -m gesture_control   # module execution
"""

from __future__ import annotations

import collections
import ctypes
import os
import sys
import threading
import time

import cv2
import numpy as np

# --- Allow direct execution (python main.py) by adding project root to sys.path ---
# This must happen BEFORE any relative imports to support direct execution.
try:
    # When run as module (python -m gesture_control), this works
    from .engine import GestureEngine, _HAND_CONNECTIONS
    from .actions import ListeningFlag, SystemVolume, MouseControl, switch_window
    from .contracts import ActionIntent, FrameSnapshot
    from .pointing import PointingGate
    from .paired_modes import PairedModes, _open as _open_hand
    from .custom_gestures import (CustomRecognizer, GestureFeedback, PoseSample,
                                  PORTAL_TEMPLATE, load_profiles)
    from .target_app import FocusGuard, ShortcutExecutor
    from .voice import start_voice_listener
    from .ui import launch_settings_ui
    from .settings import load_settings, save_settings, _sanitize
except ImportError:
    # When run as script (python main.py), relative imports fail
    # Add the project root (parent of gesture_control/) to sys.path
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    # Now use absolute imports
    from gesture_control.engine import GestureEngine, _HAND_CONNECTIONS
    from gesture_control.actions import ListeningFlag, SystemVolume, MouseControl, switch_window
    from gesture_control.contracts import ActionIntent, FrameSnapshot
    from gesture_control.pointing import PointingGate
    from gesture_control.paired_modes import PairedModes, _open as _open_hand
    from gesture_control.custom_gestures import (CustomRecognizer, GestureFeedback, PoseSample,
                                                 PORTAL_TEMPLATE, load_profiles)
    from gesture_control.target_app import FocusGuard, ShortcutExecutor
    from gesture_control.voice import start_voice_listener
    from gesture_control.ui import launch_settings_ui
    from gesture_control.settings import load_settings, save_settings, _sanitize

_WINDOW_NAME = "NAVI // WIRED - Gesture Daemon"
_PREVIEW_W = 480
_PREVIEW_H = 480
# Stop OS input if inference has not produced a fresh hand frame.
_MAX_INPUT_FRAME_AGE = 0.25


def _emergency_pause_pressed(key_state=None) -> bool:
    """Poll Ctrl+Shift+F12 across windows; never inject OS input here."""
    if key_state is None:
        try:
            key_state = ctypes.windll.user32.GetAsyncKeyState
        except (AttributeError, OSError):
            return False
    try:
        return all(key_state(code) & 0x8000 for code in (0x11, 0x10, 0x7B))
    except (OSError, ValueError):
        return False



def _bind_foreground_pressed(key_state=None) -> bool:
    """Poll Ctrl+Shift+F11 as an explicit target bind, with no key injection."""
    if key_state is None:
        try:
            key_state = ctypes.windll.user32.GetAsyncKeyState
        except (AttributeError, OSError):
            return False
    try:
        return all(key_state(code) & 0x8000 for code in (0x11, 0x10, 0x7A))
    except (OSError, ValueError):
        return False


def _set_window_always_on_top(window_name: str, on_top: bool) -> bool:
    """Toggle Windows 'always on top' for a named OpenCV window via Win32.

    OpenCV's imshow has no topmost flag. We locate the native HWND by window
    title and call SetWindowPos with HWND_TOPMOST/HWND_NOTOPMOST. No-op and
    returns False on non-Windows.
    """
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return False
    try:
        hwnd = user32.FindWindowW(None, window_name)
        if not hwnd:
            return False
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        flag = HWND_TOPMOST if on_top else HWND_NOTOPMOST
        user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        return True
    except Exception:
        return False


def _set_fullscreen(window_name: str, fullscreen: bool) -> bool:
    """Toggle fullscreen for the preview window.

    Uses the real virtual-desktop size (not just the primary monitor) so the
    fullscreen preview spans every connected display. Win32 makes the window
    borderless; OpenCV has no native fullscreen API.
    """
    try:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        if fullscreen:
            from .display import get_virtual_screen
            vs = get_virtual_screen()
            w, h = vs["width"], vs["height"]
            cv2.resizeWindow(window_name, w, h)
            cv2.moveWindow(window_name, vs["left"], vs["top"])
        else:
            cv2.resizeWindow(window_name, _PREVIEW_W, _PREVIEW_H)
        if fullscreen:
            _set_window_always_on_top(window_name, True)
        return True
    except Exception:
        return False


class GestureApp:
    """Polls the gesture engine and dispatches mapped actions.

    Discrete gestures (swipe, fist, open_palm, thumbs_up) route through
    `gesture_map`. The pinch gesture drives continuous mouse control via a
    dedicated state machine (MouseControl), so pinch is NOT part of the
    discrete routing table.
    """

    def __init__(self, settings: dict[str, any] | None = None) -> None:
        _given = settings
        # Persisted live state is never authority to arm a new process. Explicit
        # injected settings remain available for armed fake-driver test harnesses.
        self.settings = (_given if _given is not None else
                         {**load_settings(), "control_enabled": False})
        if _given is not None and "gesture_map" in _given:
            self.settings["gesture_map"] = _given["gesture_map"]
        elif "gesture_map" not in self.settings:
            self.settings["gesture_map"] = {}
        self.listening = ListeningFlag(initial=True)
        self.volume = SystemVolume()
        self.mouse = MouseControl(self.settings)
        camera_index = int(self.settings.get("camera_index", 0))
        self.engine = GestureEngine(camera_index=camera_index,
                                    dual_hand=bool(self.settings.get("dual_hand", False)))
        self.stop_event = threading.Event()
        self.voice_thread: threading.Thread | None = None
        self._cooldown: dict[str, float] = {}
        # Pinch motion trail (pixel coords, newest last). Bounded so the
        # preview overlay can't grow without limit during long sessions.
        self._trail: collections.deque = collections.deque(maxlen=32)
        self._preview_bundle: tuple[FrameSnapshot, np.ndarray | None] | None = None
        self._last_input_frame_ts: float | None = None
        self._active_primary_id: int | None = None
        self.pointing_gate = PointingGate()
        self.paired = PairedModes()
        self.custom = CustomRecognizer(load_profiles(
            self.settings.get("custom_profile", PORTAL_TEMPLATE))[0])
        self.focus_guard = FocusGuard()
        self.shortcut_executor = ShortcutExecutor()
        self._focus_session_required = False
        self._mode = "pointer"
        self._mode_reason = ""
        self._mode_error: str | None = None
        self._custom_feedback = GestureFeedback("idle", 0.0, "preview disabled")
        self._last_proposals: tuple[str, ...] = ()
        self._pointer_reason = "pointing mode off"

        # Camera display state (mutable at runtime).
        self.fullscreen = bool(self.settings.get("camera_mode") == "fullscreen")
        self.always_on_top = bool(self.settings.get("camera_always_on_top", True))

        # Discrete action routing (pinch is handled separately by MouseControl).
        self._routes: dict[str, list[str]] = {}
        for action, gesture in self.settings["gesture_map"].items():
            if gesture != "none":
                self._routes.setdefault(gesture, []).append(action)
        self._camera_retry_thread: threading.Thread | None = None
        self._pending_fullscreen: bool | None = None
        self._pending_topmost: bool | None = None

    def set_control_enabled(self, enabled: bool) -> None:
        """Persist the explicit preview/live choice before enabling OS input."""
        if enabled and (not self.engine.is_camera_ok() or
                        (self._camera_retry_thread is not None and
                         self._camera_retry_thread.is_alive())):
            raise ValueError("camera unavailable or restarting; show your hand and retry")
        updated = {**self.settings, "control_enabled": bool(enabled)}
        self.pointing_gate.reset()
        self.paired.reset()
        self.custom.reset()
        self.focus_guard.clear()
        self._focus_session_required = False
        self._mode = "pointer"
        self._active_primary_id = None
        self._pointer_reason = "pointing dwell" if enabled else "paused"
        if not enabled:
            # Safety beats persistence: release the OS first, even if the
            # settings file is read-only or the disk is full.
            self.mouse.set_control_enabled(False)
            self.settings = updated
            self._cooldown.clear()
            save_settings(updated)
            return
        save_settings(updated)
        self.settings = updated
        self.mouse.set_control_enabled(True)
        self._cooldown.clear()

    def request_camera_retry(self, index: int | None = None) -> bool:
        """Retry in the background so Tk never freezes while probing cameras."""
        if self._camera_retry_thread is not None and self._camera_retry_thread.is_alive():
            return False
        self.mouse.set_control_enabled(False)
        self.pointing_gate.reset()
        self.paired.reset()
        self.custom.reset()
        self.focus_guard.clear()
        self._focus_session_required = False
        self._mode = "pointer"
        self._active_primary_id = None
        self._pointer_reason = "camera retry"
        if self.settings.get("control_enabled"):
            self.settings = {**self.settings, "control_enabled": False}
            save_settings(self.settings)
        preferred = int(index) if index is not None else None
        self._camera_retry_thread = threading.Thread(
            target=self.engine.restart,
            kwargs={"camera_index": preferred,
                    "dual_hand": bool(self.settings.get("dual_hand", False))},
            name="camera-retry", daemon=True)
        self._camera_retry_thread.start()
        return True

    def apply_settings(self, settings: dict) -> None:
        """Apply safe tuning, mapping and camera selection without relaunching."""
        updated = _sanitize(settings)
        old_index = self.settings.get("camera_index", 0)
        index_changed = old_index != updated["camera_index"]
        dual_changed = bool(self.settings.get("dual_hand", False)) != bool(updated.get("dual_hand", False))
        retrying = (self._camera_retry_thread is not None and
                    self._camera_retry_thread.is_alive())
        if (not self.mouse.control_enabled or index_changed or dual_changed or retrying or
                not self.engine.is_camera_ok()):
            # Saving settings must not implicitly grant OS input. Only the
            # explicit Enable Control action is allowed to do that.
            updated["control_enabled"] = False
        self.mouse.apply_settings(updated)
        if self.mouse.control_enabled != updated["control_enabled"]:
            self.mouse.set_control_enabled(updated["control_enabled"])
        self.settings = updated
        self.pointing_gate.reset()
        self.paired.reset()
        self.custom = CustomRecognizer(load_profiles(updated["custom_profile"])[0])
        self.focus_guard.clear()
        self._focus_session_required = False
        self._mode = "pointer"
        self._active_primary_id = None
        self._pointer_reason = "pointing dwell" if updated["require_pointing_pose"] else "pointing mode off"
        self._routes = {}
        for action, gesture in updated["gesture_map"].items():
            if gesture != "none":
                self._routes.setdefault(gesture, []).append(action)
        self._cooldown.clear()
        if index_changed or dual_changed:
            self.request_camera_retry(updated["camera_index"])
        fullscreen = updated["camera_mode"] == "fullscreen"
        if fullscreen != self.fullscreen:
            self._pending_fullscreen = fullscreen
        topmost = updated["camera_always_on_top"]
        if topmost != self.always_on_top:
            self._pending_topmost = topmost

    def apply_tuning(self, key: str, value: object) -> None:
        """Live-preview a field; the Save button persists it."""
        if key not in self.settings:
            return
        self.apply_settings({**self.settings, key: value})

    def start_voice(self) -> None:
        """Start the background voice listener thread."""
        self.voice_thread = start_voice_listener(
            self.listening,
            self.settings["wake_phrase"],
            self.settings["stop_phrase"],
            self.stop_event,
        )

    def _dispatch(self, action: str) -> None:
        """Execute a single mapped action."""
        if action == "volume_up":
            level = self.volume.adjust(+self.settings["volume_step_percent"])
            if level is not None:
                print(f"[action] volume up -> {level}%")
        elif action == "volume_down":
            level = self.volume.adjust(-self.settings["volume_step_percent"])
            if level is not None:
                print(f"[action] volume down -> {level}%")
        elif action == "window_prev":
            if switch_window(previous=True):
                print("[action] window_prev (Alt+Tab)")
        elif action == "window_next":
            if switch_window(previous=False):
                print("[action] window_next (Alt+Shift+Tab)")

    def _dispatch_discrete_gesture(self, gesture: str, now: float) -> tuple[ActionIntent, ...]:
        """Propose inert actions first; only execute if armed and past cooldown."""
        armed = self.listening.get() and self.mouse.control_enabled
        proposals = tuple(ActionIntent(source=gesture, action=action,
                                       armed=armed, timestamp=now)
                          for action in self._routes.get(gesture, ()))
        if not armed:
            return proposals
        cooldown = self.settings["cooldown_seconds"]
        if now - self._cooldown.get(gesture, 0.0) < cooldown:
            return ()
        self._cooldown[gesture] = now
        for intent in proposals:
            self._dispatch(intent.action)
        return proposals

    def _mouse_step(
        self,
        now: float,
        *,
        capture_ts: float = 0.0,
        tips: dict | None = None,
    ) -> None:
        """Feed only explicitly supplied same-snapshot tips to the mouse.

        An omitted tip set means tracking loss, never fetch a newer inference
        and combine it with an unrelated capture timestamp.
        """
        if not self.listening.get():
            self.mouse.release()
            self._trail.clear()
            return

        if tips is None:
            self.mouse.update(None, now=now, capture_ts=capture_ts)
            self._trail.clear()
            return
        self.mouse.update(tips, now=now, capture_ts=capture_ts)

        index_tip = tips.get("index_tip") if isinstance(tips, dict) else None
        if index_tip is not None:
            h, w = self.mouse._last_frame_size if hasattr(self.mouse, "_last_frame_size") else (480, 480)
            px = int(index_tip[0] * w)
            py = int(index_tip[1] * h)
            self._trail.append((px, py))

    def _execute_paired(self, action: str) -> bool:
        """Arbiter's sole output path for two-hand proposals, never preview."""
        if not (self.mouse.control_enabled and self.listening.get()):
            return False
        try:
            if action in ("volume_up", "volume_down"):
                step = self.settings["volume_step_percent"] * (1 if action == "volume_up" else -1)
                level = self.volume.adjust(step)
                if level is None:
                    self._mode_error = "volume endpoint unavailable"
                    return False
                self._mode_reason = f"volume {level}%"
                self._mode_error = None
                return True
            if action in ("zoom_in", "zoom_out", "tab_next", "tab_previous"):
                if not self.focus_guard.focus_ok():
                    self._mode_reason = "bind supported foreground app: Ctrl+Shift+F11"
                    return False
                ok = self.shortcut_executor.execute(action, guard=self.focus_guard,
                                                    armed=self.mouse.control_enabled)
                self._mode_reason = "shortcut sent" if ok else "unsupported app/shortcut or focus lost"
                if not ok:
                    self._mode_error = self._mode_reason
                return ok
        except Exception as exc:
            self._mode_error = f"paired action failed: {exc}"
            self.mouse.set_control_enabled(False)
        return False

    def bind_foreground(self) -> bool:
        """Explicit, ephemeral target bind; never infer a target from camera focus."""
        if not (self.settings.get("dual_hand") and self.settings.get("paired_actions")):
            return False
        self.paired.reset()
        self.mouse.release()
        ok = self.focus_guard.bind_foreground()
        self._focus_session_required = ok
        self._mode_reason = "target bound" if ok else "unsupported or preview foreground"
        return ok

    def choose_primary(self, track_id: int) -> bool:
        """Operator-selected stable role; release first and reacquire before input."""
        if not self.engine.choose_primary(track_id):
            return False
        self.mouse.release()
        self.pointing_gate.reset()
        self.paired.reset()
        self.custom.reset()
        self.focus_guard.clear()
        self._focus_session_required = False
        self._active_primary_id = None
        self._last_input_frame_ts = None
        self._mode = "pointer"
        self._pointer_reason = "primary selection; reacquiring"
        return True

    def _handle_modes(self, snapshot: FrameSnapshot, now: float) -> bool:
        """Custom preview > paired/secondary > primary; True suppresses pointer."""
        self._last_proposals = ()
        primary = snapshot.primary_hand
        secondary = snapshot.secondary_hand if snapshot.secondary_accepted else None
        if not self.settings.get("dual_hand", False):
            self.paired.reset()
            self.custom.reset()
            self._mode = "pointer"
            return False
        if self.settings.get("custom_preview", False):
            samples = {}
            for role, hand in (("primary", primary), ("secondary", secondary)):
                if hand is not None:
                    points = hand.landmarks
                    center = (sum(points[i][0] for i in (0, 5, 9, 13, 17)) / 5,
                              sum(points[i][1] for i in (0, 5, 9, 13, 17)) / 5)
                    samples[role] = PoseSample("open_palm" if _open_hand(hand) else "other",
                                               center, hand.track_id)
            self._custom_feedback = self.custom.update(samples, snapshot.timestamp,
                                                       armed=False)
            if self._custom_feedback.cue in ("start", "circle", "hold", "finish",
                                             "cooldown", "release_to_rearm"):
                self.paired.reset()
                self.mouse.release()
                self.pointing_gate.reset()
                self._mode = "custom"
                self._mode_reason = self._custom_feedback.reason
                return True
            if self._mode == "custom":
                self.mouse.release()
                self.pointing_gate.reset()
                self._mode = "pointer"
                return True  # one neutral frame after cancelling a macro
        else:
            self.custom.reset()
            self._custom_feedback = GestureFeedback("idle", 0.0, "preview disabled")
        if self.settings.get("paired_actions", False):
            if secondary is None and self._mode in ("volume", "paired", "latched"):
                self.paired.reset()
                self.mouse.release()
                self.pointing_gate.reset()
                self._mode = "pointer"
                self._mode_reason = "secondary lost; release before rearm"
                return True
            focus_ok = (not self._focus_session_required or self.focus_guard.focus_ok())
            proposals = self.paired.update(primary, secondary, snapshot.timestamp,
                                           armed=self.listening.get(), focus_ok=focus_ok)
            if not focus_ok:
                self._mode_reason = "foreground changed: bind again"
            mode = self.paired._mode
            if mode is not None:
                self._mode = mode
                self.mouse.release()
                self.pointing_gate.reset()
                self._last_proposals = tuple(intent.action for intent in proposals)
                if proposals and not self.mouse.control_enabled:
                    self._mode_reason = "preview only: " + ", ".join(self._last_proposals)
                for intent in proposals:
                    self._execute_paired(intent.action)
                return True
        else:
            self.paired.reset()
        self._mode = "pointer"
        return False

    def _consume_snapshot(self, snapshot: FrameSnapshot, now: float) -> None:
        """One inference drives discrete routing and mouse arbitration together."""
        age = now - snapshot.timestamp
        if (not snapshot.hand_accepted or snapshot.tips is None
                or age < 0 or age > _MAX_INPUT_FRAME_AGE):
            # Only a gate-accepted, fresh hand can drive any OS action.
            # Grace tips remain visual-only, even with stale gesture metadata.
            self.pointing_gate.reset()
            self.paired.reset()
            self.custom.reset()
            self._mode = "pointer"
            self._last_proposals = ()
            self._active_primary_id = None
            self._pointer_reason = snapshot.rejection_reason or "no fresh pointing hand"
            self._mouse_step(now, capture_ts=snapshot.capture_ts, tips=None)
            return
        if (self._last_input_frame_ts is not None
                and snapshot.timestamp <= self._last_input_frame_ts):
            # UI polling must not turn one inference into multiple actions.
            return
        self._last_input_frame_ts = snapshot.timestamp
        if self.settings.get("dual_hand", False):
            track_id = snapshot.primary_track_id
            if track_id is None:
                self.mouse.release()
                self._pointer_reason = "no primary role"
                return
            if self._active_primary_id is not None and self._active_primary_id != track_id:
                self.mouse.release()
                self.pointing_gate.reset()
            self._active_primary_id = track_id
        if self._handle_modes(snapshot, now):
            return
        selected_tips = dict(snapshot.tips)
        if self.settings.get("require_pointing_pose", False):
            selected_tips, self._pointer_reason = self.pointing_gate.filter(
                snapshot.primary_hand if self.settings.get("dual_hand", False)
                else snapshot.hands[0] if snapshot.hands else None,
                selected_tips, snapshot.timestamp)
            if selected_tips is None:
                self._mouse_step(now, capture_ts=snapshot.capture_ts, tips=None)
                return
        else:
            self._pointer_reason = "pointing mode off"
        gesture = snapshot.legacy_gesture(now)
        if gesture and gesture != "pinch":
            self._dispatch_discrete_gesture(gesture, now)
        self._mouse_step(now, capture_ts=snapshot.capture_ts,
                         tips=selected_tips)

    def shutdown(self) -> None:
        """Stop all subsystems: voice, engine, and mouse buttons."""
        self.stop_event.set()
        self.mouse.release()
        if self.voice_thread is not None and self.voice_thread.is_alive():
            self.voice_thread.join(timeout=2.0)
        self.engine.stop()


def _draw_preview(app: GestureApp, blank: np.ndarray) -> np.ndarray:
    """Build the annotated preview frame: Navi Eyego // WIRED HUD.

    Same contract as before (returns a BGR frame for cv2.imshow); only the
    visual treatment changed: void-black bars, phosphor-green telemetry,
    amber gesture readout, CRT scanlines, and Navi corner brackets.
    """
    # Local import with direct-execution fallback (mirrors the top of file).
    try:
        from .lain_theme import (
            BGR_AMBER as _AMBER, BGR_GHOST as _GHOST, BGR_MUTED as _MUTED,
            BGR_PHOSPHOR as _PHOS, BGR_RED as _RED, BGR_VOID as _VOID,
        )
    except ImportError:  # pragma: no cover - direct script execution
        from gesture_control.lain_theme import (
            BGR_AMBER as _AMBER, BGR_GHOST as _GHOST, BGR_MUTED as _MUTED,
            BGR_PHOSPHOR as _PHOS, BGR_RED as _RED, BGR_VOID as _VOID,
        )

    bundle = app._preview_bundle
    snapshot, frame = bundle if bundle is not None else app.engine.get_frame_bundle()
    pts = snapshot.pixel_landmarks
    status = snapshot.status_dict()
    cam_ok = status["camera_ok"]
    fps = status["fps"]

    if frame is None:
        frame = blank.copy()
        frame[:] = _VOID
        msg = "// tuning to the wired..." if cam_ok else "// NO CARRIER - camera unavailable"
        color = _PHOS if cam_ok else _RED
        cv2.putText(frame, "PRESENT DAY - PRESENT TIME", (20, 200),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, _MUTED, 1, lineType=cv2.LINE_AA)
        cv2.putText(frame, msg, (20, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, lineType=cv2.LINE_AA)
        return frame

    h, w = frame.shape[:2]
    app.mouse._last_frame_size = (h, w)

    # CRT scanlines: darken every 3rd row for phosphor-monitor feel.
    frame[::3] = (frame[::3].astype(np.uint16) * 88 // 100).astype(np.uint8)

    # Wired tint: lift greens slightly toward phosphor.
    frame[:, :, 1] = np.clip(
        frame[:, :, 1].astype(np.int16) + 8, 0, 255).astype(np.uint8)

    # --- top status bar (void black, phosphor telemetry) ---
    frame[:44, :] = _VOID
    cv2.line(frame, (0, 44), (w, 44), _PHOS, 1)

    listening = app.listening.get()
    voice_color = _PHOS if listening else _RED
    status_text = "// WIRED: LISTENING" if listening else "// WIRED: PAUSED"
    cv2.putText(frame, status_text, (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, voice_color, 2, lineType=cv2.LINE_AA)

    cv2.putText(frame, f"cam{status['camera_index']}  {fps:.0f}fps", (w - 145, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, _GHOST if cam_ok else _RED,
                1, lineType=cv2.LINE_AA)

    # --- bottom telemetry bar ---
    frame[h - 52:, :] = _VOID
    cv2.line(frame, (0, h - 52), (w, h - 52), _PHOS, 1)

    raw_gesture = snapshot.legacy_gesture(time.monotonic())
    gesture_label = (raw_gesture or "---").upper()
    gesture_color = _AMBER if raw_gesture else _MUTED
    cv2.putText(frame, f"SIG:{gesture_label}", (12, h - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, gesture_color, 2, lineType=cv2.LINE_AA)

    action = app.mouse.action()
    action_label = action.upper() if action else "IDLE"
    action_color = _PHOS if action else _MUTED
    cv2.putText(frame, f"ACT:{action_label}", (200, h - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, action_color, 2, lineType=cv2.LINE_AA)

    tracking_label = ("HAND OK" if status["hand_tracked"] else
                      "REJECTED" if status["hand_detected"] else "NO HAND")
    tracking_color = _PHOS if status["hand_tracked"] else _RED
    cv2.putText(frame, tracking_label, (w - 104, h - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, tracking_color, 1,
                lineType=cv2.LINE_AA)
    if status["hand_detected"] and status["rejection_reason"]:
        cv2.putText(frame, f"REJECT:{status['rejection_reason']}", (12, 88),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _RED, 1,
                    lineType=cv2.LINE_AA)
    if app.settings.get("require_pointing_pose"):
        cv2.putText(frame, f"POINTER:{app._pointer_reason}", (12, 112),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _AMBER, 1,
                    lineType=cv2.LINE_AA)

    # Stable roles only: green primary, amber secondary; rejected roles red.
    if app.settings.get("dual_hand"):
        for label, hand, accepted, color in (
                ("P", snapshot.primary_hand, snapshot.hand_accepted, _PHOS),
                ("S", snapshot.secondary_hand, snapshot.secondary_accepted, _AMBER)):
            if hand is None:
                continue
            color = color if accepted else _RED
            role_pts = tuple((min(w - 1, max(0, int(p[0] * w))),
                              min(h - 1, max(0, int(p[1] * h))))
                             for p in hand.landmarks)
            if len(role_pts) != 21:
                continue
            for a, b in _HAND_CONNECTIONS:
                cv2.line(frame, role_pts[a], role_pts[b], color, 2, lineType=cv2.LINE_AA)
            for point in role_pts:
                cv2.circle(frame, point, 3, color, -1, lineType=cv2.LINE_AA)
            wx, wy = role_pts[0]
            cv2.putText(frame, f"{label}{hand.track_id}", (wx, max(52, wy - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, .48, color, 1,
                        lineType=cv2.LINE_AA)
        cv2.putText(frame, f"MODE:{app._mode} {app._mode_reason}"[:55], (12, 137),
                    cv2.FONT_HERSHEY_SIMPLEX, .42, _AMBER, 1, lineType=cv2.LINE_AA)
        if app.settings.get("custom_preview") and app._custom_feedback.cue != "idle":
            progress = max(0.0, min(1.0, app._custom_feedback.progress))
            cv2.rectangle(frame, (12, 149), (172, 156), _MUTED, 1)
            cv2.rectangle(frame, (12, 149), (12 + int(160 * progress), 156),
                          _AMBER, -1)
            cv2.putText(frame, app._custom_feedback.cue.upper(), (180, 157),
                        cv2.FONT_HERSHEY_SIMPLEX, .42, _AMBER, 1,
                        lineType=cv2.LINE_AA)
    elif pts:
        for a, b in _HAND_CONNECTIONS:
            cv2.line(frame, pts[a], pts[b], _PHOS, 2, lineType=cv2.LINE_AA)
        for x, y in pts:
            cv2.circle(frame, (x, y), 3, _AMBER, -1, lineType=cv2.LINE_AA)

    # Pinch motion trail (amber fade, white-hot head).
    trail = list(app._trail)
    if len(trail) >= 2:
        for i in range(1, len(trail)):
            ratio = i / max(1, len(trail) - 1)
            thickness = 1 + int(ratio * 3)
            color = (int(30 * ratio), int(120 + 136 * ratio), int(200 + 55 * ratio))
            cv2.line(frame, trail[i - 1], trail[i], color, thickness, lineType=cv2.LINE_AA)
        cv2.circle(frame, trail[-1], 6, _GHOST, -1, lineType=cv2.LINE_AA)

    # Navi corner brackets over the viewport (Eyego frame).
    T, B = 46, h - 54
    arm = 22
    for (x0, y0, dx, dy) in ((8, T, 1, 1), (w - 8, T, -1, 1),
                             (8, B, 1, -1), (w - 8, B, -1, -1)):
        cv2.line(frame, (x0, y0), (x0 + dx * arm, y0), _PHOS, 2, lineType=cv2.LINE_AA)
        cv2.line(frame, (x0, y0), (x0, y0 + dy * arm), _PHOS, 2, lineType=cv2.LINE_AA)

    # Action badge, top-right below the status bar.
    banner = action_label
    (tw, th), _ = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
    bx, by = w - tw - 16, 68
    cv2.rectangle(frame, (bx - 8, by - th - 8), (bx + tw + 8, by + 8), _VOID, -1)
    cv2.rectangle(frame, (bx - 8, by - th - 8), (bx + tw + 8, by + 8), action_color, 1)
    cv2.putText(frame, banner, (bx, by), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                action_color, 2, lineType=cv2.LINE_AA)

    cursor = app.mouse._last_cursor
    if cursor is not None:
        cv2.putText(frame, f"cursor {cursor[0]},{cursor[1]} // present time",
                    (12, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, _MUTED, 1,
                    lineType=cv2.LINE_AA)
    else:
        cv2.putText(frame, "PRESENT DAY - PRESENT TIME", (12, h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, _MUTED, 1, lineType=cv2.LINE_AA)

    return frame


def _run_preview(app: GestureApp) -> None:
    """Own the OpenCV window and camera loop on a dedicated worker thread."""
    # Clean startup/camera-init sequence with progress indication in overlay.
    blank = np.zeros((_PREVIEW_H, _PREVIEW_W, 3), dtype=np.uint8)
    cv2.namedWindow(_WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(_WINDOW_NAME, _PREVIEW_W, _PREVIEW_H)
    cv2.imshow(_WINDOW_NAME, blank)
    cv2.waitKey(1)

    phases = [
        "// tuning to the wired...",
        "// opening visual cortex",
        "// waking the hand seer",
        "// loading gesture daemons",
        "// present day - present time",
    ]
    total_steps = 50
    try:
        from .lain_theme import (
            BGR_GHOST as _BOOT_GHOST, BGR_MUTED as _BOOT_MUTED,
            BGR_PHOSPHOR as _BOOT_PHOS, BGR_VOID as _BOOT_VOID,
        )
    except ImportError:  # pragma: no cover - direct script execution
        from gesture_control.lain_theme import (
            BGR_GHOST as _BOOT_GHOST, BGR_MUTED as _BOOT_MUTED,
            BGR_PHOSPHOR as _BOOT_PHOS, BGR_VOID as _BOOT_VOID,
        )
    for idx in range(total_steps):
        phase_idx = min(idx // 10, len(phases) - 1)
        progress_text = phases[phase_idx]
        bar = "=" * idx + ">" + " " * (total_steps - idx - 1)
        blank[:] = _BOOT_VOID
        cv2.putText(blank, "PRESENT DAY - PRESENT TIME", (16, 210),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, _BOOT_MUTED, 1,
                    lineType=cv2.LINE_AA)
        cv2.putText(blank, progress_text, (16, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, _BOOT_GHOST, 2,
                    lineType=cv2.LINE_AA)
        cv2.putText(blank, bar, (16, 282),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, _BOOT_PHOS, 2,
                    lineType=cv2.LINE_AA)
        pct = int((idx / (total_steps - 1)) * 100)
        cv2.putText(blank, f"{pct}%", (_PREVIEW_W - 50, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, _BOOT_PHOS, 1,
                    lineType=cv2.LINE_AA)
        cv2.imshow(_WINDOW_NAME, blank)
        key = cv2.waitKey(25) & 0xFF
        if key in (ord("q"), 27):
            app.stop_event.set()
            break
        time.sleep(0.06)

    if app.stop_event.is_set():
        cv2.destroyAllWindows()
        app.shutdown()
        return

    print("[run] Waiting for camera to initialize...")
    camera_ready = False
    for _ in range(300):  # 30 second timeout, or fail fast on a terminal error
        if app.stop_event.is_set():
            break
        if app.engine.is_camera_ok():
            camera_ready = True
            break
        if app.engine.get_tracking_status()["state"] == "ERROR":
            break
        time.sleep(0.1)

    if app.stop_event.is_set():
        cv2.destroyAllWindows()
        app.shutdown()
        return

    if not camera_ready:
        print("[run] Camera unavailable. Preview stays open; retry in the Navi panel.")

    if app.fullscreen:
        _set_fullscreen(_WINDOW_NAME, True)
    else:
        cv2.resizeWindow(_WINDOW_NAME, _PREVIEW_W, _PREVIEW_H)
    if app.always_on_top:
        _set_window_always_on_top(_WINDOW_NAME, True)

    hint = ("q=quit  Esc=exit-fullscreen/quit  f=fullscreen  a=on-top  "
            "Ctrl+Shift+F11=BIND TARGET  Ctrl+Shift+F12=PAUSE")
    print(f"[run] {hint}")
    bind_key_was_down = False

    try:
        while not app.stop_event.is_set():
            if app._pending_fullscreen is not None:
                requested = app._pending_fullscreen
                app._pending_fullscreen = None
                _set_fullscreen(_WINDOW_NAME, requested)
                app.fullscreen = requested
            if app._pending_topmost is not None:
                requested = app._pending_topmost
                app._pending_topmost = None
                _set_window_always_on_top(_WINDOW_NAME, requested)
                app.always_on_top = requested
            now = time.monotonic()
            bind_key_down = _bind_foreground_pressed()
            if bind_key_down and not bind_key_was_down:
                print("[run] foreground target " +
                      ("bound" if app.bind_foreground() else "rejected (focus/feature)"))
            bind_key_was_down = bind_key_down
            if _emergency_pause_pressed() and app.mouse.control_enabled:
                try:
                    app.set_control_enabled(False)
                    print("[run] emergency pause: OS control disabled")
                except Exception as exc:
                    # The emergency path must still release input if writing
                    # settings fails or any other callback raises.
                    app.mouse.set_control_enabled(False)
                    print(f"[run] emergency pause (settings not saved): {exc}")

            # Action and HUD use the same inference/pixels, not separate reads.
            app._preview_bundle = app.engine.get_frame_bundle()
            app._consume_snapshot(app._preview_bundle[0], now)
            try:
                frame = _draw_preview(app, blank)
            finally:
                app._preview_bundle = None
            cv2.imshow(_WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                app.stop_event.set()
                break
            elif key == 27:  # Esc: exit fullscreen first, then quit if already normal
                if app.fullscreen:
                    app.fullscreen = False
                    _set_fullscreen(_WINDOW_NAME, False)
                    if not app.always_on_top:
                        _set_window_always_on_top(_WINDOW_NAME, False)
                    print("[run] exited fullscreen (Esc); press Esc again to quit")
                else:
                    app.stop_event.set()
                    break
            elif key == ord("f"):
                app.fullscreen = not app.fullscreen
                _set_fullscreen(_WINDOW_NAME, app.fullscreen)
                print(f"[run] fullscreen {'ON' if app.fullscreen else 'OFF'}")
            elif key == ord("a"):
                app.always_on_top = not app.always_on_top
                ok = _set_window_always_on_top(_WINDOW_NAME, app.always_on_top)
                print(f"[run] always-on-top {'ON' if app.always_on_top else 'OFF'}"
                      f"{' (unsupported)' if not ok else ''}")

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\n[run] interrupted.")
    finally:
        cv2.destroyAllWindows()
        app.shutdown()
        print("[run] all systems stopped.")


def _ui_status(app: GestureApp) -> dict:
    """Tk reads a single metadata snapshot; Tk widgets stay on its own thread."""
    snap = app.engine.get_snapshot()
    return _project_status(app, snap)


def _project_status(app: GestureApp, snap: FrameSnapshot) -> dict:
    return {**snap.status_dict(), "frame_id": snap.frame_id,
            "gesture": snap.legacy_gesture(time.monotonic()),
            "action": app.mouse.action(),
            "pointing_reason": app._pointer_reason,
            "require_pointing_pose": bool(app.settings.get("require_pointing_pose")),
            "mode": app._mode, "mode_reason": app._mode_reason,
            "custom_cue": app._custom_feedback.cue,
            "custom_progress": app._custom_feedback.progress,
            "proposals": app._last_proposals,
            "control_enabled": app.mouse.control_enabled,
            "input_error": app.mouse.input_error or app._mode_error}


def _panel_bundle(app: GestureApp) -> tuple[dict, np.ndarray | None]:
    """Tk receives detached pixels and metadata from the same locked inference."""
    snapshot, frame = app.engine.get_frame_bundle()
    return _project_status(app, snapshot), frame


def main() -> None:
    """Run Tk on the main thread and the OpenCV preview in a worker."""
    app = GestureApp()
    app.engine.start()
    app.start_voice()

    def preview_worker() -> None:
        try:
            _run_preview(app)
        except Exception as exc:
            print(f"[run] preview failed: {exc!r}")
        finally:
            app.stop_event.set()
            app.shutdown()

    preview_thread = threading.Thread(target=preview_worker, name="camera-preview", daemon=True)
    preview_thread.start()
    try:
        launch_settings_ui(
            app.listening.get,
            on_closed=app.stop_event.set,
            stop_event=app.stop_event,
            frame_provider=app.engine.get_latest_frame,
            on_control_toggle=app.set_control_enabled,
            on_tuning_change=app.apply_tuning,
            on_apply_settings=app.apply_settings,
            on_camera_retry=app.request_camera_retry,
            on_choose_primary=app.choose_primary,
            status_provider=lambda: _ui_status(app),
            bundle_provider=lambda: _panel_bundle(app),
        )
    finally:
        app.stop_event.set()
        preview_thread.join(timeout=5.0)
        app.shutdown()


def run_debug() -> None:
    """Camera preview with landmarks and the detected gesture (no voice or Tkinter UI).

    Useful for quick visual debugging of hand tracking.
    """
    engine = GestureEngine()
    engine.start()
    print("[debug] Waiting for camera to initialize...")
    camera_ready = False
    for _ in range(300):  # 30 second timeout
        if engine.is_camera_ok():
            camera_ready = True
            break
        time.sleep(0.1)

    if not camera_ready:
        print("[debug] ERROR: Camera failed to initialize within 30 seconds.")
        print("[debug] Check that a camera is connected and Windows camera privacy settings allow access.")
        engine.stop()
        return
    print("[debug] q/Esc=quit")
    # Create a temporary MouseControl instance for mouse state management
    temp_mouse = MouseControl(load_settings())
    blank = np.zeros((_PREVIEW_H, _PREVIEW_W, 3), dtype=np.uint8)
    try:
        while True:
            snapshot, frame = engine.get_frame_bundle()
            pts = snapshot.pixel_landmarks
            fps = snapshot.fps
            gesture = snapshot.legacy_gesture(time.monotonic())

            if frame is None:
                frame = blank.copy()
                frame[:] = (7, 6, 5)
                cv2.putText(frame, "// tuning to the wired...", (20, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (102, 255, 51), 2,
                            lineType=cv2.LINE_AA)
            else:
                if pts:
                    for a, b in _HAND_CONNECTIONS:
                        cv2.line(frame, pts[a], pts[b], (102, 255, 51), 2,
                                 lineType=cv2.LINE_AA)
                    for x, y in pts:
                        cv2.circle(frame, (x, y), 3, (0, 176, 255), -1,
                                   lineType=cv2.LINE_AA)
                cv2.putText(frame, f"SIG:{gesture or '---'}", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 176, 255), 2,
                            lineType=cv2.LINE_AA)
                cv2.putText(frame, f"FPS:{fps:.0f} // WIRED", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (216, 224, 215), 1,
                            lineType=cv2.LINE_AA)

            cv2.imshow(_WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[debug] interrupted.")
    finally:
        cv2.destroyAllWindows()
        temp_mouse.release()
        engine.stop()
        print("[debug] stopped.")


if __name__ == "__main__":
    main()
