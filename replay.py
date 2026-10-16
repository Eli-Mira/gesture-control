"""Deterministic gesture-intent replay; this module never imports OS drivers."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .gesture_state import FrameObservation, GestureStateMachine


def replay_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replay ordered timestamped observations in simulation-only mode."""
    machine = GestureStateMachine()
    previous = -math.inf
    output: list[dict[str, Any]] = []
    fields = set(FrameObservation.__slots__)
    for frame in events:
        timestamp = float(frame["t"])
        if not math.isfinite(timestamp) or timestamp <= previous:
            raise ValueError("trace timestamps must be finite and strictly increasing")
        previous = timestamp
        raw_hand = frame.get("hand")
        if raw_hand is None:
            hand = None
        elif isinstance(raw_hand, dict):
            unexpected = set(raw_hand) - fields
            if unexpected:
                raise ValueError(f"unknown hand fields: {sorted(unexpected)}")
            hand = FrameObservation(**raw_hand)
        else:
            raise ValueError("hand must be an observation object or null")
        output.append({"t": timestamp, **machine.update(hand, now=timestamp), "simulation": True})
    return output


def main() -> None:
    """Print simulated intents from JSONL without opening a camera or OS driver."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="JSONL with t and hand observation fields")
    path = parser.parse_args().trace
    with path.open(encoding="utf-8") as stream:
        frames = [json.loads(line) for line in stream if line.strip()]
    print(json.dumps(replay_events(frames), indent=2))


if __name__ == "__main__":
    main()
