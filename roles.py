"""Conservative, camera-free hand identity and role tracking.

Consumes already accepted landmarks; it never controls the detector or OS input.
The caller must release any held button when primary is missing, ambiguous,
changes ID, or reset() is called. Occlusion, mirror ambiguity and abrupt motion
cannot establish physical identity: ambiguous matches revoke roles rather than
promising continuity. A lost secondary is not automatically reassigned; reset
(or an explicit new calibration workflow) is needed for a new paired session.
The geometric thresholds are provisional and require supervised tuning.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .contracts import HandObservation


@dataclass(frozen=True, slots=True)
class RoleFrame:
    primary: HandObservation | None
    secondary: HandObservation | None
    ambiguous: bool = False


@dataclass(slots=True)
class _Track:
    hand: HandObservation
    seen: int
    since: float
    last_time: float
    wrist_velocity: tuple[float, float] = (0.0, 0.0)
    palm_velocity: tuple[float, float] = (0.0, 0.0)


def _palm(hand: HandObservation) -> tuple[float, float]:
    points = hand.landmarks
    indices = (0, 5, 9, 13, 17) if len(points) > 17 else (0,)
    return (sum(points[i][0] for i in indices) / len(indices),
            sum(points[i][1] for i in indices) / len(indices))


def _match_cost(track: _Track, hand: HandObservation, timestamp: float) -> float:
    dt = timestamp - track.last_time
    if not 0 < dt <= 0.25 or not track.hand.landmarks or not hand.landmarks:
        return math.inf
    wrist = track.hand.landmarks[0][:2]
    now_wrist = hand.landmarks[0][:2]
    speed = math.hypot(*track.wrist_velocity)
    if math.dist(wrist, now_wrist) > min(0.30, max(0.18, speed * dt + 0.08)):
        return math.inf
    palm = _palm(track.hand)
    now_palm = _palm(hand)
    expected_wrist = tuple(wrist[i] + track.wrist_velocity[i] * dt for i in (0, 1))
    expected_palm = tuple(palm[i] + track.palm_velocity[i] * dt for i in (0, 1))
    cost = (math.dist(expected_wrist, now_wrist) +
            math.dist(expected_palm, now_palm)) / 2
    if (track.hand.handedness_hint and hand.handedness_hint and
            track.hand.handedness_hint != hand.handedness_hint):
        cost += 0.004  # Deliberately weaker than spatial/motion evidence.
    return cost


class RoleTracker:
    """Return only sufficiently persistent, unambiguously identified roles."""

    def __init__(self) -> None:
        self._next_id = 1
        self.reset()

    def reset(self) -> None:
        self._tracks: dict[int, _Track] = {}
        self._primary_id: int | None = None
        self._secondary_id: int | None = None
        self._has_chosen_primary = False
        self._has_chosen_secondary = False

    def update(self, hands: tuple[HandObservation, ...], timestamp: float) -> RoleFrame:
        if len(hands) > 2:
            raise ValueError("at most two hands per frame")
        previous = list(self._tracks.values())
        ambiguous = (len(hands) == 2 and bool(hands[0].landmarks)
                     and bool(hands[1].landmarks)
                     and math.dist(hands[0].landmarks[0][:2], hands[1].landmarks[0][:2]) < 0.06
                     and math.dist(_palm(hands[0]), _palm(hands[1])) < 0.06)
        if len(previous) == 2 and len(hands) == 1:
            costs = sorted(_match_cost(track, hands[0], timestamp) for track in previous)
            ambiguous |= math.isfinite(costs[1]) and costs[1] - costs[0] < 0.012
        if len(previous) == 2 and len(hands) == 2:
            direct = sum(_match_cost(previous[i], hands[i], timestamp) for i in (0, 1))
            swapped = sum(_match_cost(previous[i], hands[1 - i], timestamp) for i in (0, 1))
            ambiguous |= (math.isfinite(direct) and math.isfinite(swapped)
                          and abs(direct - swapped) < 0.015)
        if ambiguous:
            # No prior identity survives a tie, even if the hint says Left/Right.
            self._tracks = {}
            self._primary_id = None
            self._secondary_id = None
            self._has_chosen_primary = True
            self._has_chosen_secondary = True
        previous = list(self._tracks.values())
        assignment: dict[int, _Track] = {}
        if len(previous) == len(hands) == 2:
            direct = sum(_match_cost(previous[i], hands[i], timestamp) for i in (0, 1))
            swapped = sum(_match_cost(previous[i], hands[1 - i], timestamp) for i in (0, 1))
            if math.isfinite(min(direct, swapped)):
                assignment = ({0: previous[0], 1: previous[1]} if direct <= swapped
                              else {0: previous[1], 1: previous[0]})
        tracks: dict[int, _Track] = {}
        for index, hand in enumerate(hands):
            track = assignment.get(index)
            if track is None and not assignment:
                eligible = [(cost, candidate) for candidate in previous
                            if math.isfinite(cost := _match_cost(candidate, hand, timestamp))]
                if eligible:
                    _, track = min(eligible, key=lambda pair: pair[0])
            if track is not None:
                previous.remove(track)
            if track is None:
                track_id = self._next_id
                self._next_id += 1
                track = _Track(hand, 0, timestamp, timestamp)
            else:
                track_id = track.hand.track_id
                dt = timestamp - track.last_time
                old_wrist, new_wrist = track.hand.landmarks[0], hand.landmarks[0]
                old_palm, new_palm = _palm(track.hand), _palm(hand)
                track.wrist_velocity = tuple((new_wrist[i] - old_wrist[i]) / dt
                                             for i in (0, 1))
                track.palm_velocity = tuple((new_palm[i] - old_palm[i]) / dt
                                            for i in (0, 1))
            observed = replace(hand, track_id=track_id)
            track.hand = observed
            track.seen += 1
            track.last_time = timestamp
            tracks[track_id] = track
        self._tracks = tracks
        if self._primary_id is not None and self._primary_id not in tracks:
            self._primary_id = None
        if self._secondary_id is not None and self._secondary_id not in tracks:
            self._secondary_id = None
        stable = [t for t in tracks.values() if t.seen >= 3 and timestamp - t.since >= 0.10]
        stable_ids = {t.hand.track_id for t in stable}
        if self._primary_id is None and not self._has_chosen_primary and stable:
            self._primary_id = stable[0].hand.track_id
            self._has_chosen_primary = True
        if self._primary_id is not None and self._secondary_id is None and not self._has_chosen_secondary:
            self._secondary_id = next((t.hand.track_id for t in stable
                                       if t.hand.track_id != self._primary_id), None)
            self._has_chosen_secondary = self._secondary_id is not None
        primary = tracks[self._primary_id].hand if self._primary_id in stable_ids else None
        secondary = tracks[self._secondary_id].hand if self._secondary_id in stable_ids else None
        return RoleFrame(None, None, True) if ambiguous else RoleFrame(primary, secondary)

    def choose_primary(self, track_id: int) -> bool:
        track = self._tracks.get(track_id)
        if track is None or track.seen < 3 or track.last_time - track.since < 0.10:
            return False
        if track_id != self._primary_id:
            old_primary = self._primary_id
            if track_id == self._secondary_id:
                self._secondary_id = old_primary if old_primary in self._tracks else None
        self._primary_id = track_id
        self._has_chosen_primary = True
        return True
