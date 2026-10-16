"""Pure per-hand pose geometry; no camera, UI, temporal state, or OS imports."""
from __future__ import annotations

from typing import Any

PINCH_MAX_RATIO = 0.45
EXTEND_RATIO = 1.12
THUMBS_UP_MARGIN = 0.55


def distance(a: Any, b: Any) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def classify_static(lm: list[Any]) -> str | None:
    wrist = lm[0]
    hand_size = max(distance(lm[9], wrist), 1e-6)
    thumb_out = distance(lm[4], lm[17]) > distance(lm[3], lm[17]) * 1.08
    four_extended = sum(distance(lm[t], wrist) > distance(lm[p], wrist) * EXTEND_RATIO
                        for t, p in ((8, 6), (12, 10), (16, 14), (20, 18)))
    if distance(lm[4], lm[8]) / hand_size < PINCH_MAX_RATIO:
        return "pinch"
    if (thumb_out and four_extended == 0
            and lm[2].y - lm[4].y > hand_size * THUMBS_UP_MARGIN):
        return "thumbs_up"
    if four_extended == 0:
        return "fist"
    if four_extended >= 4:
        return "open_palm"
    return None


def extract_pose(lm: list[Any], *, width: int, height: int) -> tuple:
    """Return legacy static label, palm center, pixel skeleton and raw tips."""
    static = classify_static(lm)
    center = (sum(lm[i].x for i in (0, 5, 9, 13, 17)) / 5.0,
              sum(lm[i].y for i in (0, 5, 9, 13, 17)) / 5.0)
    points = [(int(p.x * width), int(p.y * height)) for p in lm]
    hand_size = distance(lm[9], lm[0])
    tips = {
        "thumb_tip": (lm[4].x, lm[4].y),
        "index_tip": (lm[8].x, lm[8].y),
        "middle_tip": (lm[12].x, lm[12].y),
        "ring_tip": (lm[16].x, lm[16].y),
        "hand_size": max(hand_size, 1e-6),
        "thumb_index_dist": distance(lm[4], lm[8]),
        "thumb_middle_dist": distance(lm[4], lm[12]),
        "thumb_ring_dist": distance(lm[4], lm[16]),
    }
    return static, center, points, tips
