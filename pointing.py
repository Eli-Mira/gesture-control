"""Opt-in pointer-pose gate; accepted hands need not control the cursor."""
from __future__ import annotations

import math
from collections.abc import Mapping

from .contracts import HandObservation


class PointingGate:
    """Two-frame pointer dwell with a short cursor anchor during click pinches."""

    def __init__(self, dwell: float = 0.06, anchor_timeout: float = 0.35) -> None:
        self.dwell = dwell
        self.anchor_timeout = anchor_timeout
        self.reset()

    def reset(self) -> None:
        self._candidate_since: float | None = None
        self._candidate_frames = 0
        self._active = False
        self._anchor: tuple[float, float] | None = None
        self._pinch_since: float | None = None
        self._last_time: float | None = None

    @staticmethod
    def _pointing(hand: HandObservation) -> bool:
        points = hand.landmarks
        if len(points) != 21:
            return False
        wrist = points[0][:2]
        distance = lambda i: math.dist(points[i][:2], wrist)
        if distance(8) <= distance(6) * 1.12:
            return False
        return all(distance(tip) <= distance(pip) * 1.08
                   for tip, pip in ((12, 10), (16, 14), (20, 18)))

    def filter(self, hand: HandObservation | None, tips: Mapping | None,
               timestamp: float) -> tuple[dict | None, str]:
        if (hand is None or tips is None or tips.get('index_tip') is None
                or self._last_time is not None
                and (timestamp <= self._last_time or timestamp - self._last_time > 0.25)):
            self.reset()
            return None, 'no fresh pointing hand'
        self._last_time = timestamp
        if self._pointing(hand):
            self._pinch_since = None
            if self._candidate_since is None:
                self._candidate_since = timestamp
                self._candidate_frames = 0
            self._candidate_frames += 1
            if self._candidate_frames < 2 or timestamp - self._candidate_since < self.dwell:
                return None, 'pointing dwell'
            self._active = True
            self._anchor = tuple(tips['index_tip'])
            return dict(tips), 'pointing'
        size = float(tips.get('hand_size') or 0.0)
        pinch = size > 0 and float(tips.get('thumb_index_dist') or 0.0) / size < 0.40
        if self._active and pinch and self._anchor is not None:
            if self._pinch_since is None:
                self._pinch_since = timestamp
            if timestamp - self._pinch_since <= self.anchor_timeout:
                anchored = dict(tips)
                anchored['index_tip'] = self._anchor
                return anchored, 'pinch anchor'
        self.reset()
        return None, 'not pointing'
