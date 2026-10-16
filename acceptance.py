"""Conservative single-track landmark acceptance before any action projection.

Geometry limits are provisional safety checks, not a guarantee of rejecting faces.
No face-region mask is used: a genuine hand can be held over a face.
"""
from __future__ import annotations

import math

from .contracts import HandObservation


class HandGate:
    """Require plausible observations and several continuous frames to acquire."""

    def __init__(self) -> None:
        self._candidate_since: float | None = None
        self._candidate_count = 0
        self._last_center: tuple[float, float] | None = None
        self._last_scale = 0.0
        self._last_time: float | None = None
        self._accepted = False
        self._lost_at: float | None = None

    def reset(self) -> None:
        self._candidate_since = None
        self._candidate_count = 0
        self._last_center = None
        self._last_time = None
        self._accepted = False
        self._lost_at = None

    def evaluate(self, hand: HandObservation | None, timestamp: float) -> tuple[bool, str | None]:
        if hand is None:
            if self._last_time is not None and self._lost_at is None:
                self._lost_at = timestamp
            self._candidate_since = None
            self._candidate_count = 0
            self._last_center = None
            self._last_time = None
            self._accepted = False
            return False, "no hand"
        reason, center, scale = self._geometry(hand)
        if reason is not None:
            if self._lost_at is None and self._last_time is not None:
                self._lost_at = timestamp
            self._candidate_since = None
            self._candidate_count = 0
            self._last_center = None
            self._last_time = None
            self._accepted = False
            return False, reason
        if self._lost_at is not None and timestamp - self._lost_at < 0.15:
            return False, "re-acquiring"
        if self._lost_at is not None:
            self._lost_at = None
        if self._last_time is not None:
            jump = math.dist(center, self._last_center)
            if (timestamp <= self._last_time or timestamp - self._last_time > 0.25
                    or jump > max(0.18, self._last_scale * 1.5)
                    or scale / self._last_scale > 2.5
                    or self._last_scale / scale > 2.5):
                self._candidate_since = None
                self._candidate_count = 0
                self._accepted = False
                self._last_center = center
                self._last_scale = scale
                self._last_time = timestamp
                return False, "motion discontinuity"
        if self._candidate_since is None:
            self._candidate_since = timestamp
        self._candidate_count += 1
        self._last_center, self._last_scale, self._last_time = center, scale, timestamp
        self._accepted = (self._candidate_count >= 3
                          and timestamp - self._candidate_since >= 0.10)
        return (True, None) if self._accepted else (False, "stabilizing")

    @staticmethod
    def _geometry(hand: HandObservation) -> tuple[str | None, tuple[float, float], float]:
        points = hand.landmarks
        if len(points) != 21 or any(len(p) != 3 for p in points):
            return "landmark count", (0.0, 0.0), 0.0
        if any(not all(math.isfinite(v) for v in p) for p in points):
            return "non-finite landmark", (0.0, 0.0), 0.0
        if any(not 0.0 <= p[0] <= 1.0 or not 0.0 <= p[1] <= 1.0 for p in points):
            return "outside frame", (0.0, 0.0), 0.0
        xy = [(p[0], p[1]) for p in points]
        scale = math.dist(xy[0], xy[9])
        if not 0.035 <= scale <= 0.65:
            return "implausible palm scale", (0.0, 0.0), 0.0
        spread = math.dist(xy[5], xy[17])
        if spread < max(0.025, scale * 0.30) or spread > scale * 3.0:
            return "implausible palm geometry", (0.0, 0.0), 0.0
        for root, pip in ((5, 6), (9, 10), (13, 14), (17, 18)):
            segment = math.dist(xy[root], xy[pip])
            if not scale * 0.05 <= segment <= scale * 2.5:
                return "implausible finger geometry", (0.0, 0.0), 0.0
        center = (sum(xy[i][0] for i in (0, 5, 9, 13, 17)) / 5,
                  sum(xy[i][1] for i in (0, 5, 9, 13, 17)) / 5)
        return None, center, scale
