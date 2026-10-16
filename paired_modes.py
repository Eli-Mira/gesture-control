"""Inert two-hand motion proposals; never touches camera, UI, or OS input.

Coordinates are normalized camera-frame coordinates: y decreases upward. x
increases rightward in the supplied coordinates. With ``mirror_x=False`` a
rightward motion in those coordinates means next tab; set ``mirror_x=True``
when the source's left/right orientation is reversed relative to the intended
*display* direction. Distance-based zoom is unchanged by mirroring. Handedness
labels never determine direction or roles. The pose/threshold choices are
provisional synthetic defaults, not calibrated from the user's camera.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from .contracts import HandObservation


def _valid(hand: HandObservation) -> bool:
    return (len(hand.landmarks) == 21
            and all(len(point) == 3 and all(isfinite(v) for v in point)
                    and 0 <= point[0] <= 1 and 0 <= point[1] <= 1
                    for point in hand.landmarks))


@dataclass(frozen=True, slots=True)
class ModeProposal:
    """A relative, inert step for an external focus-guarded arbiter/executor.

    ``amount`` is a signed-neutral count (always positive); ``action`` supplies
    direction. These proposals do not contain OS shortcuts or an absolute volume
    target. The eventual executor must read actual volume/mute state, enforce
    endpoint bounds and errors, and map zoom/tabs for an allowed foreground app.
    """

    source: str
    action: str
    armed: bool
    timestamp: float
    amount: int = 1


def _center(hand: HandObservation) -> tuple[float, float]:
    return (sum(hand.landmarks[i][0] for i in (0, 5, 9, 13, 17)) / 5,
            sum(hand.landmarks[i][1] for i in (0, 5, 9, 13, 17)) / 5)


def _modifier(hand: HandObservation) -> bool:
    points = hand.landmarks
    size = hypot(points[9][0] - points[0][0], points[9][1] - points[0][1])
    pinch = hypot(points[4][0] - points[8][0], points[4][1] - points[8][1])
    extended = all(
        hypot(points[tip][0] - points[0][0], points[tip][1] - points[0][1])
        > 1.12 * hypot(points[pip][0] - points[0][0], points[pip][1] - points[0][1])
        for tip, pip in ((12, 10), (16, 14), (20, 18))
    )
    return size > 0.025 and pinch < size * .35 and extended


def _open(hand: HandObservation) -> bool:
    points = hand.landmarks
    size = hypot(points[9][0] - points[0][0], points[9][1] - points[0][1])
    if size <= .025 or _modifier(hand):
        return False
    return all(
        hypot(points[tip][0] - points[0][0], points[tip][1] - points[0][1])
        > 1.12 * hypot(points[pip][0] - points[0][0], points[pip][1] - points[0][1])
        for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18))
    )


class PairedModes:
    """Frame-to-frame recognizer; callers assign stable primary/secondary roles."""

    def __init__(self, *, mirror_x: bool = False) -> None:
        self.mirror_x = mirror_x
        self.reset()
        self._needs_release = False  # No held pose has been observed at startup.

    def reset(self) -> None:
        """Clear pending recognition; require a visible release before rearming."""
        self._needs_release = True
        self._mode: str | None = None
        self._since = 0.0
        self._last_y = 0.0
        self._travel = 0.0
        self._last_emit = float('-inf')
        self._pair_start = (0.0, 0.0)
        self._hand_start = (0.0, 0.0)
        self._span_start = 0.0
        self._last_time: float | None = None
        self._track_ids: tuple[int | None, int | None] | None = None

    def update(self, primary: HandObservation | None,
               secondary: HandObservation | None, timestamp: float, *,
               armed: bool, focus_ok: bool) -> tuple[ModeProposal, ...]:
        if not (armed and focus_ok and primary is not None and secondary is not None):
            self.reset()
            return ()
        if (not isfinite(timestamp) or not _valid(primary) or not _valid(secondary)
                or (self._last_time is not None and timestamp <= self._last_time)):
            self.reset()
            return ()
        ids = primary.track_id, secondary.track_id
        if (ids[0] is not None and ids[0] == ids[1]
                or (self._track_ids is not None
                    and any(old is not None and new is not None and old != new
                            for old, new in zip(self._track_ids, ids)))):
            self.reset()
            return ()
        self._last_time = timestamp
        self._track_ids = ids
        modifier = _modifier(secondary)
        paired = _open(primary) and _open(secondary)
        desired = 'volume' if modifier else 'paired' if paired else None
        if self._needs_release:
            if desired is None:
                self.reset()
                self._needs_release = False
            return ()
        if desired != self._mode:
            if self._mode is not None:
                self.reset()
                if desired is None:
                    self._needs_release = False
                return ()
            if desired is None:
                return ()
            self._mode = desired
            self._since = timestamp
            if desired == 'volume':
                self._last_y = _center(secondary)[1]
            else:
                p, s = _center(primary), _center(secondary)
                if hypot(s[0] - p[0], s[1] - p[1]) < .14 or abs(s[0] - p[0]) < .10:
                    self.reset()
                    return ()
                self._pair_start = ((p[0] + s[0]) / 2, (p[1] + s[1]) / 2)
                self._hand_start = (p[0], s[0])
                self._span_start = hypot(s[0] - p[0], s[1] - p[1])
            return ()
        if timestamp - self._since < .10:
            if desired == 'volume':
                self._last_y = _center(secondary)[1]
            return ()
        if desired == 'paired':
            p, s = _center(primary), _center(secondary)
            # Overlap/crossing makes role correspondence and span ambiguous.
            if (hypot(s[0] - p[0], s[1] - p[1]) < .14
                    or (s[0] - p[0]) * (self._hand_start[1] - self._hand_start[0]) <= 0):
                self.reset()
                return ()
            span_delta = hypot(s[0] - p[0], s[1] - p[1]) - self._span_start
            center_dx = (p[0] + s[0]) / 2 - self._pair_start[0]
            center_dy = (p[1] + s[1]) / 2 - self._pair_start[1]
            if abs(span_delta) >= .085 and abs(center_dx) <= .055 and abs(center_dy) <= .055:
                self._mode = 'latched'
                self._needs_release = True
                return (ModeProposal('paired_modes',
                                     'zoom_in' if span_delta > 0 else 'zoom_out',
                                     armed, timestamp),)
            if (abs(center_dx) >= .12 and abs(span_delta) <= .035
                    and abs(center_dy) <= .055
                    and (p[0] - self._hand_start[0]) * center_dx > 0
                    and (s[0] - self._hand_start[1]) * center_dx > 0):
                self._mode = 'latched'
                self._needs_release = True
                return (ModeProposal('paired_modes',
                                     ('tab_previous' if center_dx > 0 else 'tab_next')
                                     if self.mirror_x else
                                     ('tab_next' if center_dx > 0 else 'tab_previous'),
                                     armed, timestamp),)
            return ()
        sy = _center(secondary)[1]
        delta = sy - self._last_y
        self._last_y = sy
        if abs(delta) > .20:
            self._travel = 0.0
            return ()
        self._travel += delta
        if abs(self._travel) < .025 or timestamp - self._last_emit < .15:
            return ()
        direction = 'volume_up' if self._travel < 0 else 'volume_down'
        self._travel = 0.0  # Discard overshoot; a stationary hold never repeats a step.
        self._last_emit = timestamp
        return (ModeProposal('paired_modes', direction, armed, timestamp),)
