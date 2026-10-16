"""Gesture detection engine using MediaPipe HandLandmarker."""

from __future__ import annotations

import collections
import os
import threading
import time
from typing import Any

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
from .acceptance import HandGate
from .contracts import FrameSnapshot, HandObservation
from .features import classify_static, distance, extract_pose
from .roles import RoleTracker
from .temporal import detect_swipe, smooth_tips

# --- tunables ---
CAMERA_WIDTH = 480
CAMERA_HEIGHT = 480

HISTORY_LEN = 10
SWIPE_MIN_SAMPLES = 6
SWIPE_MIN_TRAVEL = 0.16
SWIPE_AXIS_MARGIN = 1.25
SWIPE_TTL = 0.30

PINCH_MAX_RATIO = 0.45
EXTEND_RATIO = 1.12
THUMBS_UP_MARGIN = 0.55

_MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hand_landmarker.task")

# Landmark indices
_WRIST, _THUMB_TIP, _INDEX_TIP = 0, 4, 8
_THUMB_MCP, _THUMB_IP = 2, 3
_INDEX_MCP, _MIDDLE_MCP, _RING_MCP, _PINKY_MCP = 5, 9, 13, 17
_FINGER_PIPS = {8: 6, 12: 10, 16: 14, 20: 18}
_HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


class GestureEngine:
    """Hand gesture detection engine running in a background thread."""

    def __init__(self, camera_index: int = 0, *, dual_hand: bool = False) -> None:
        self._camera_index = camera_index
        self._dual_hand = bool(dual_hand)
        self._num_hands = 2 if self._dual_hand else 1
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        # Shared state
        self._camera_ok = False
        self._static_gesture: str | None = None
        self._swipe: tuple[str, float] | None = None
        self._hand_pos: tuple[float, float] | None = None
        self._landmarks: list[tuple[int, int]] | None = None
        self._tips: dict | None = None
        self._frame_buffers = [None, None]
        self._frame_index = 0
        self._fps = 0.0
        # Per-frame latency diagnostics (seconds, monotonic): time from
        # cap.read() returning to (a) inference done, (b) lock handoff.
        self._capture_ts: float = 0.0
        self._infer_ts: float = 0.0
        # Operator-facing state: landmark depth is not a confidence score.
        self._status = "STARTING"
        self._last_error: str | None = None
        self._hand_tracked = False
        self._snapshot = FrameSnapshot(camera_index=camera_index)
        self._frame_id = 0
        self._gate = HandGate()
        self._secondary_gate = HandGate()
        self._roles = RoleTracker()
        self._gate_track_id: int | None = None
        self._pending_primary_id: int | None = None

        # Tracking-loss grace period (seconds). When the hand is briefly lost
        # (fast movement, occlusion) we keep returning the last known tips so
        # the cursor/buttons don't instantaneously snap or release.
        self._tracking_grace = 0.30
        self._last_hand_time = 0.0

        # Temporal smoothing of fingertip positions (EMA alpha).
        self._tip_smoothing = 0.5
        self._smoothed_tips: dict | None = None

    def _dist(self, a: Any, b: Any) -> float:
        """Euclidean distance between two landmark-like objects with x/y."""
        return distance(a, b)

    def _smooth_tips(self, tips: dict) -> dict:
        """Exponentially smooth fingertip positions to reduce tracking jitter.

        Only 2D tip coordinates are smoothed (not hand_size / distances,
        which are computed from the already-smoothed tip estimates anyway).
        """
        out = smooth_tips(tips, self._smoothed_tips, alpha=float(self._tip_smoothing))
        self._smoothed_tips = out
        return dict(out)

    def _extended(self, lm: Any, tip: int, pip: int, wrist: Any) -> bool:
        """Return True if the fingertip is clearly extended past the PIP."""
        return self._dist(lm[tip], wrist) > self._dist(lm[pip], wrist) * EXTEND_RATIO

    def _classify_static(self, lm: list[Any]) -> str | None:
        """Pose classification for a single frame of landmarks."""
        return classify_static(lm)

    def _detect_swipe(self, history: collections.deque) -> str | None:
        """Detect a swipe gesture from a history of (ts, x, y) samples."""
        return detect_swipe(history)

    def _publish_frame(self, frame: np.ndarray, result: Any, *, timestamp: float,
                       capture_ts: float, infer_ts: float,
                       history: collections.deque | None = None,
                       fps_t0: float | None = None, owned_frame: bool = False) -> None:
        """Extract one inference and commit its immutable projection in one lock.

        Production passes its exclusively owned cv2.flip output; external callers
        get a defensive frame copy. Pixels are copied on read only for consumers.
        """
        if history is None:
            history = collections.deque(maxlen=HISTORY_LEN)
        static, pos, pts, swipe, tips = None, None, None, None, None
        hands = tuple(
            HandObservation.from_landmarks(
                lm, timestamp=timestamp,
                handedness_hint=(result.handedness[i][0].category_name
                                 if i < len(getattr(result, "handedness", ()))
                                 and result.handedness[i] else None),
            )
            for i, lm in enumerate(result.hand_landmarks[:2])
        )
        primary = secondary = None
        secondary_accepted = ambiguous = False
        primary_index = 0
        if self._dual_hand:
            # Bad geometry must not enter identity matching (NaN can poison
            # pairwise costs). Raw detections remain visible in the snapshot.
            valid = tuple(hand for hand in hands if HandGate._geometry(hand)[0] is None)
            if not valid:
                # A fully empty frame is an explicit safe session boundary:
                # after all hands leave, a new primary may be acquired.
                self._roles.reset()
            role_frame = self._roles.update(valid, timestamp)
            primary, secondary, ambiguous = (role_frame.primary,
                                              role_frame.secondary, role_frame.ambiguous)
            with self._lock:
                requested = self._pending_primary_id
                self._pending_primary_id = None
            if requested is not None:
                # The UI never mutates camera-thread identity state directly.
                self._roles.choose_primary(requested)
                primary = secondary = None
                ambiguous = True  # force one safe frame before reacquisition
            current_id = primary.track_id if primary is not None else None
            if current_id != self._gate_track_id:
                self._gate.reset()
                self._secondary_gate.reset()
                self._smoothed_tips = None
                history.clear()
                self._gate_track_id = current_id
            accepted, rejection = self._gate.evaluate(primary, timestamp)
            secondary_accepted, _ = self._secondary_gate.evaluate(secondary, timestamp)
            if ambiguous:
                rejection = "ambiguous hand identity"
            elif primary is None and hands:
                rejection = ("primary lost; remove all hands to reset" if secondary
                             else "awaiting stable primary role")
            if primary is not None:
                primary_index = next(i for i, hand in enumerate(hands)
                                     if hand.landmarks == primary.landmarks)
        else:
            accepted, rejection = self._gate.evaluate(hands[0] if hands else None, timestamp)
            primary = hands[0] if accepted else None
        if accepted:
            lm = result.hand_landmarks[primary_index]
            h, w = frame.shape[:2]
            static, pos, pts, raw_tips = extract_pose(lm, width=w, height=h)
            history.append((timestamp, *pos))
            swipe = self._detect_swipe(history)
            if swipe:
                history.clear()
            tips = self._smooth_tips(raw_tips)
        else:
            history.clear()
            self._smoothed_tips = None

        with self._lock:
            self._capture_ts = capture_ts
            self._infer_ts = infer_ts
            if tips is not None:
                self._last_hand_time = time.monotonic()
            if tips is None:
                self._swipe = None
            elif swipe is not None:
                self._swipe = (swipe, timestamp + SWIPE_TTL)
            elif self._swipe is not None and timestamp >= self._swipe[1]:
                self._swipe = None
            self._static_gesture = static
            self._hand_pos = pos
            self._landmarks = pts
            self._tips = tips
            self._hand_tracked = tips is not None
            self._status = ("HAND TRACKED" if accepted else
                            "HAND REJECTED" if hands else "SEARCHING FOR HAND")
            self._last_error = None
            back_idx = 1 - self._frame_index
            self._frame_buffers[back_idx] = frame if owned_frame else frame.copy()
            self._frame_index = back_idx
            if fps_t0 is not None:
                dt = timestamp - fps_t0
                if dt > 0:
                    self._fps = 0.9 * self._fps + 0.1 / dt
            self._frame_id += 1
            self._snapshot = FrameSnapshot(
                timestamp=timestamp, hands=hands, camera_ok=self._camera_ok,
                status=self._status, error=None, camera_index=self._camera_index,
                fps=self._fps, capture_ts=capture_ts, infer_ts=infer_ts,
                frame_id=self._frame_id, frame_size=(frame.shape[1], frame.shape[0]),
                gesture=self._swipe[0] if self._swipe else static,
                static_gesture=static,
                swipe_until=self._swipe[1] if self._swipe else None,
                hand_pos=pos, pixel_landmarks=pts, tips=tips,
                grace_tips=self._smoothed_tips, last_hand_ts=self._last_hand_time,
                hand_accepted=accepted, rejection_reason=rejection if hands else None,
                primary_hand=primary, secondary_hand=secondary,
                secondary_accepted=secondary_accepted, role_ambiguous=ambiguous,
                primary_track_id=primary.track_id if primary else None,
                secondary_track_id=secondary.track_id if secondary else None,
            )

    def choose_primary(self, track_id: int) -> bool:
        """Queue a stable visible role switch for the camera thread, disarming one frame."""
        with self._lock:
            if (not self._dual_hand or type(track_id) is not int
                    or track_id not in (self._snapshot.primary_track_id,
                                        self._snapshot.secondary_track_id)):
                return False
            self._pending_primary_id = track_id
            return True

    def get_snapshot(self) -> FrameSnapshot:
        """Return one coherent, immutable per-inference metadata snapshot."""
        with self._lock:
            return self._snapshot

    def get_frame_bundle(self) -> tuple[FrameSnapshot, np.ndarray | None]:
        """Return snapshot and independently writable pixels from the same frame."""
        with self._lock:
            frame = self._frame_buffers[self._frame_index]
            return self._snapshot, frame.copy() if frame is not None else None

    def _publish_status(self, status: str, error: str | None,
                        *, camera_ok: bool) -> None:
        with self._lock:
            self._publish_status_unlocked(status, error, camera_ok=camera_ok)

    def _publish_status_unlocked(self, status: str, error: str | None,
                                 *, camera_ok: bool) -> None:
        """Clear stale frame/hand data when capture changes state (lock held)."""
        self._camera_ok = camera_ok
        self._status = status
        self._last_error = error
        self._hand_tracked = False
        self._gate.reset()
        self._secondary_gate.reset()
        self._roles.reset()
        self._gate_track_id = None
        self._pending_primary_id = None
        self._smoothed_tips = None
        self._last_hand_time = 0.0
        self._static_gesture = None
        self._hand_pos = None
        self._landmarks = None
        self._tips = None
        self._swipe = None
        self._frame_buffers[0] = None
        self._frame_buffers[1] = None
        self._snapshot = FrameSnapshot(timestamp=time.monotonic(), status=status,
                                       error=error, camera_ok=camera_ok,
                                       camera_index=self._camera_index, fps=self._fps)

    def _open_camera(self) -> cv2.VideoCapture | None:
        """Try the preferred camera first, then discover another usable index."""
        preferred = self._camera_index
        for index in (preferred, *(i for i in range(5) if i != preferred)):
            for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
                cap = None
                keep = False
                try:
                    cap = cv2.VideoCapture(index, backend)
                    if not cap.isOpened():
                        continue
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
                    time.sleep(0.1)
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        with self._lock:
                            self._camera_index = index
                            self._publish_status_unlocked("SEARCHING FOR HAND", None,
                                                          camera_ok=True)
                        print(f"[gesture_engine] Camera {index} opened with backend {backend}")
                        keep = True
                        return cap
                except Exception as exc:
                    print(f"[gesture_engine] Camera {index} backend {backend} failed: {exc}")
                finally:
                    if cap is not None and not keep:
                        cap.release()
        self._publish_status("ERROR", "No available camera (checked indexes 0-4)",
                             camera_ok=False)
        return None

    def _engine_loop(self) -> None:
        """Background thread: capture frames, run inference, publish state."""
        MAX_RECONNECT_ATTEMPTS = 5
        RECONNECT_DELAY = 2.0
        reconnect_attempts = 0

        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=self._num_hands,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        landmarker = vision.HandLandmarker.create_from_options(options)

        history = collections.deque(maxlen=HISTORY_LEN)
        fps_t0 = time.monotonic()
        self._fps = 0.0

        cap = self._open_camera()
        if cap is None:
            print("[gesture_engine] ERROR: no available camera. Check Windows privacy settings.")
            landmarker.close()
            return

        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    print("[gesture_engine] WARNING: camera read failed, attempting reconnect...")
                    cap.release()
                    with self._lock:
                        self._publish_status_unlocked("RECONNECTING",
                                                      "Camera read failed; reconnecting",
                                                      camera_ok=False)
                    while reconnect_attempts < MAX_RECONNECT_ATTEMPTS and not self._stop.is_set():
                        reconnect_attempts += 1
                        print(f"[gesture_engine] Reconnect attempt {reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} in {RECONNECT_DELAY}s...")
                        time.sleep(RECONNECT_DELAY)
                        cap = self._open_camera()
                        if cap is not None:
                            reconnect_attempts = 0
                            break

                    if cap is None or self._stop.is_set():
                        print("[gesture_engine] ERROR: max reconnect attempts reached, giving up.")
                        break
                    continue

                frame = cv2.flip(frame, 1)
                capture_end = time.monotonic()
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                timestamp_ms = int(time.monotonic() * 1000)

                result = landmarker.detect_for_video(
                    mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                    timestamp_ms,
                )
                infer_end = time.monotonic()

                now = time.monotonic()
                self._publish_frame(frame, result, timestamp=now,
                                    capture_ts=capture_end, infer_ts=infer_end,
                                    history=history, fps_t0=fps_t0, owned_frame=True)
                fps_t0 = now
        except Exception as exc:
            with self._lock:
                self._publish_status_unlocked("ERROR", f"Detection failed: {exc}",
                                              camera_ok=False)
            print(f"[gesture_engine] detection loop crashed: {exc!r}")
        finally:
            try:
                cap.release()
            except Exception:
                pass
            try:
                landmarker.close()
            except Exception:
                pass
            with self._lock:
                self._camera_ok = False
                self._static_gesture = None
                self._hand_pos = None
                self._landmarks = None
                self._tips = None
                self._smoothed_tips = None
                self._frame_buffers[0] = None
                self._frame_buffers[1] = None
                self._swipe = None
                self._capture_ts = 0.0
                self._infer_ts = 0.0
                self._hand_tracked = False
                if self._stop.is_set() and self._status != "ERROR":
                    self._status = "STOPPED"
                self._publish_status_unlocked(self._status, self._last_error,
                                              camera_ok=False)

    def _run_engine_loop(self) -> None:
        try:
            self._engine_loop()
        except Exception as exc:
            with self._lock:
                self._publish_status_unlocked("ERROR",
                                              f"Camera/model initialization failed: {exc}",
                                              camera_ok=False)
            print(f"[gesture_engine] initialization failed: {exc!r}")

    def start(self) -> None:
        """Start the detection thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not os.path.isfile(_MODEL_PATH):
                raise FileNotFoundError(
                    f"hand_landmarker.task not found at {_MODEL_PATH}. Download it from "
                    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
                    "hand_landmarker/float16/latest/hand_landmarker.task")
            self._stop.clear()
            with self._lock:
                self._publish_status_unlocked("STARTING", None, camera_ok=False)
            self._thread = threading.Thread(target=self._run_engine_loop, name="gesture-engine", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop the detection thread and release the camera."""
        self._stop.set()
        thread_ref = self._thread
        if thread_ref is not None and thread_ref.is_alive():
            thread_ref.join(timeout=3.0)
        # Keep ownership until the old thread actually exits; otherwise a
        # retry could start a second reader on the same camera.
        self._thread = thread_ref if thread_ref is not None and thread_ref.is_alive() else None
        with self._lock:
            self._frame_buffers[0] = None
            self._frame_buffers[1] = None
            if self._thread is None:
                self._publish_status_unlocked("STOPPED", None, camera_ok=False)

    def restart(self, camera_index: int | None = None, *, dual_hand: bool | None = None) -> bool:
        """Retry capture without allowing two threads to own one camera."""
        old_thread = self._thread
        self.stop()
        if old_thread is not None and old_thread.is_alive():
            with self._lock:
                self._publish_status_unlocked("ERROR", "Camera is still busy; retry in a moment",
                                              camera_ok=False)
            return False
        if camera_index is not None:
            with self._lock:
                self._camera_index = int(camera_index)
        if dual_hand is not None:
            with self._lock:
                self._dual_hand = bool(dual_hand)
                self._num_hands = 2 if self._dual_hand else 1
        self.start()
        return True

    def get_gesture(self) -> str | None:
        """Latest gesture label, or None if no clear gesture right now."""
        with self._lock:
            if self._swipe is not None and time.monotonic() < self._swipe[1]:
                return self._swipe[0]
            return self._static_gesture

    def get_hand_position(self) -> tuple[float, float] | None:
        """Normalized (x, y) palm center in [0..1], or None if no hand."""
        with self._lock:
            return self._hand_pos

    def get_landmarks(self) -> list[tuple[int, int]] | None:
        """Pixel coordinates of hand landmarks for drawing."""
        with self._lock:
            return list(self._landmarks) if self._landmarks is not None else None

    def get_tips(self) -> dict | None:
        """Return fresh accepted tips only; no visual ghost may reach input."""
        with self._lock:
            return dict(self._tips) if self._tips is not None else None

    def get_tips_and_capture(self) -> dict[str, Any]:
        """Return latest tips together with the frame's capture/infer timestamps.

        All values come from a single lock acquisition, so tips and timestamps
        are guaranteed to belong to the same frame (no cross-frame mismatch).
        Used by the latency-measurement path in main.py.

        Returns:
            {"tips": dict | None, "capture_ts": float, "infer_ts": float}
        """
        with self._lock:
            return {
                "tips": dict(self._tips) if self._tips is not None else None,
                "capture_ts": self._capture_ts,
                "infer_ts": self._infer_ts,
            }

    def get_latest_frame(self) -> np.ndarray | None:
        """Compatibility accessor: an owned copy, never the live shared buffer."""
        with self._lock:
            frame = self._frame_buffers[self._frame_index]
            return frame.copy() if frame is not None else None

    def get_fps(self) -> float:
        """Latest smoothed inference FPS."""
        with self._lock:
            return self._fps

    def get_tracking_status(self) -> dict[str, Any]:
        """Operator-facing state; no fabricated numeric detection confidence."""
        with self._lock:
            return {
                "state": self._status,
                "error": self._last_error,
                "camera_ok": self._camera_ok,
                "hand_tracked": self._hand_tracked,
                "camera_index": self._camera_index,
                "fps": self._fps,
            }

    def is_camera_ok(self) -> bool:
        """True when the camera is open and producing frames."""
        with self._lock:
            return self._camera_ok
