"""Immutable per-frame observations and action proposals (never OS commands)."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class HandObservation:
    """One model observation; handedness is a hint, not a stable identity."""

    landmarks: tuple[tuple[float, float, float], ...]
    bbox: tuple[float, float, float, float]
    handedness_hint: str | None = None
    stability: float | None = None
    timestamp: float = 0.0
    track_id: int | None = None  # Unassigned until multi-hand tracking is introduced.

    def __post_init__(self) -> None:
        object.__setattr__(self, "landmarks", tuple(tuple(float(v) for v in p)
                                                 for p in self.landmarks))
        object.__setattr__(self, "bbox", tuple(self.bbox))

    @classmethod
    def from_landmarks(cls, landmarks, *, timestamp: float,
                       track_id: int | None = None, handedness_hint: str | None = None,
                       stability: float | None = None) -> HandObservation:
        coords = tuple((float(p.x), float(p.y), float(getattr(p, "z", 0.0)))
                       if hasattr(p, "x") else tuple(float(v) for v in p)
                       for p in landmarks)
        if not coords:
            raise ValueError("hand landmarks cannot be empty")
        return cls(
            landmarks=coords,
            bbox=(min(p[0] for p in coords), min(p[1] for p in coords),
                  max(p[0] for p in coords), max(p[1] for p in coords)),
            handedness_hint=handedness_hint, stability=stability,
            timestamp=timestamp, track_id=track_id,
        )


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """Atomic metadata/legacy projection for one inference frame.

    Image pixels live in the engine's separate frame buffer: get_frame_bundle()
    copies pixels under the same lock. Keeping numpy out of this object avoids
    falsely claiming a mutable array is an immutable snapshot, and avoids an
    extra 480x480x3 pixel copy on every inference tick.
    """

    timestamp: float = 0.0
    hands: tuple[HandObservation, ...] = ()
    camera_ok: bool = False
    status: str = "STARTING"
    error: str | None = None
    camera_index: int = 0
    fps: float = 0.0
    capture_ts: float = 0.0
    infer_ts: float = 0.0
    frame_id: int = 0
    frame_size: tuple[int, int] | None = None
    gesture: str | None = None
    static_gesture: str | None = None
    hand_pos: tuple[float, float] | None = None
    pixel_landmarks: tuple[tuple[int, int], ...] | None = None
    tips: Mapping[str, tuple[float, float] | float] | None = field(default=None, compare=False)
    grace_tips: Mapping[str, tuple[float, float] | float] | None = field(default=None, compare=False)
    last_hand_ts: float = 0.0
    hand_accepted: bool = False
    rejection_reason: str | None = None
    primary_hand: HandObservation | None = None
    secondary_hand: HandObservation | None = None
    secondary_accepted: bool = False
    role_ambiguous: bool = False
    primary_track_id: int | None = None
    secondary_track_id: int | None = None
    # Swipe expiry is retained so readers can project the legacy TTL at read time.
    swipe_until: float | None = None

    def __post_init__(self) -> None:
        hands = tuple(self.hands)
        if len(hands) > 2:
            raise ValueError("at most two hands per frame")
        object.__setattr__(self, "hands", hands)
        for attr in ("tips", "grace_tips"):
            value = getattr(self, attr)
            if value is not None:
                object.__setattr__(self, attr, MappingProxyType({
                    key: tuple(item) if isinstance(item, (list, tuple)) else item
                    for key, item in value.items()
                }))
        if self.pixel_landmarks is not None:
            object.__setattr__(self, "pixel_landmarks",
                               tuple(tuple(point) for point in self.pixel_landmarks))

    def legacy_gesture(self, now: float) -> str | None:
        if self.swipe_until is not None and now < self.swipe_until:
            return self.gesture
        return self.gesture if self.swipe_until is None else self.static_gesture

    def legacy_tips(self, now: float, grace: float = 0.30) -> dict | None:
        """Old control semantics: last tips linger briefly after loss (Phase 3 changes this)."""
        if self.tips is not None:
            return dict(self.tips)
        if self.grace_tips is not None and now - self.last_hand_ts <= grace:
            return dict(self.grace_tips)
        return None

    def status_dict(self) -> dict:
        return {"state": self.status, "error": self.error,
                "camera_ok": self.camera_ok, "hand_tracked": self.hand_accepted,
                "hand_detected": bool(self.hands),
                "rejection_reason": self.rejection_reason,
                "primary_track_id": self.primary_track_id,
                "secondary_track_id": self.secondary_track_id,
                "role_ambiguous": self.role_ambiguous,
                "secondary_accepted": self.secondary_accepted,
                "camera_index": self.camera_index, "fps": self.fps}


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """Typed, inert proposal for an arbiter/executor; not an OS operation."""

    source: str
    action: str
    armed: bool
    timestamp: float
