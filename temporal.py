"""Pure temporal pose projections; no capture or OS input."""
from __future__ import annotations

from collections.abc import Sequence

SWIPE_MIN_SAMPLES = 6
SWIPE_MIN_TRAVEL = 0.16
SWIPE_AXIS_MARGIN = 1.25


def detect_swipe(history: Sequence[tuple[float, float, float]]) -> str | None:
    if len(history) < SWIPE_MIN_SAMPLES:
        return None
    (_, x0, y0), (_, x1, y1) = history[0], history[-1]
    dx, dy = x1 - x0, y1 - y0
    if abs(dx) >= abs(dy) * SWIPE_AXIS_MARGIN and abs(dx) >= SWIPE_MIN_TRAVEL:
        return "swipe_right" if dx > 0 else "swipe_left"
    if abs(dy) > abs(dx) * SWIPE_AXIS_MARGIN and abs(dy) >= SWIPE_MIN_TRAVEL:
        return "swipe_up" if dy < 0 else "swipe_down"
    return None


def smooth_tips(tips: dict, previous: dict | None, *, alpha: float) -> dict:
    if previous is None:
        return dict(tips)
    out = dict(tips)
    for key in ("thumb_tip", "index_tip", "middle_tip", "ring_tip"):
        old, new = previous.get(key), tips.get(key)
        if old is not None and new is not None:
            out[key] = (old[0] + alpha * (new[0] - old[0]),
                        old[1] + alpha * (new[1] - old[1]))
    return out
