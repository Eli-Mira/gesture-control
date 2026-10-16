"""Opt-in, strictly numeric and size-bounded local gesture diagnostics.

Call ``record(engine.get_snapshot())`` only after the operator explicitly opts in.
The exporter never traverses a snapshot, frame, landmark, tip, error, or gesture:
it constructs a new allowlisted numeric record from scalar metadata only.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from threading import Lock

from .contracts import FrameSnapshot

_STATUS_CODES = {
    "STARTING": 0,
    "SEARCHING FOR HAND": 1,
    "HAND TRACKED": 2,
    "HAND REJECTED": 3,
    "STOPPED": 4,
}


def _finite(value: float) -> float:
    try:
        number = float(value)
        return round(max(0.0, min(1_000_000.0, number)), 3) if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


class DiagnosticLog:
    """Append numeric JSONL, rotating before each write; disabled by default."""

    def __init__(self, directory: Path, *, enabled: bool = False,
                 max_bytes: int = 65536, backups: int = 2) -> None:
        if not 256 <= max_bytes <= 1_048_576 or not 0 <= backups <= 5:
            raise ValueError("diagnostic limits: max_bytes 256..1048576, backups 0..5")
        self.path = Path(directory) / "diagnostics.jsonl"
        self.enabled = enabled
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = Lock()

    def record(self, snapshot: FrameSnapshot) -> bool:
        """Return False without touching disk unless explicitly enabled."""
        if not self.enabled:
            return False
        data = {
            "frame_id": max(0, int(snapshot.frame_id)),
            "status_code": _STATUS_CODES.get(snapshot.status, -1),
            "camera_ok": int(snapshot.camera_ok),
            "hand_count": min(2, len(snapshot.hands)),
            "hand_accepted": int(snapshot.hand_accepted),
            "fps": _finite(snapshot.fps),
            "infer_ms": _finite((snapshot.infer_ts - snapshot.capture_ts) * 1000),
            "pipeline_ms": _finite((snapshot.timestamp - snapshot.capture_ts) * 1000),
        }
        line = (json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
        if len(line) > self.max_bytes:
            raise ValueError("diagnostic record exceeds maximum file size")
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Do not carry an already oversized file into a numbered backup.
            for candidate in (self.path, *(Path(f"{self.path}.{i}")
                                           for i in range(1, self.backups + 1))):
                if candidate.exists() and candidate.stat().st_size > self.max_bytes:
                    candidate.unlink()
            size = self.path.stat().st_size if self.path.exists() else 0
            if size + len(line) > self.max_bytes:
                if self.backups:
                    oldest = Path(f"{self.path}.{self.backups}")
                    oldest.unlink(missing_ok=True)
                    for index in range(self.backups - 1, 0, -1):
                        source = Path(f"{self.path}.{index}")
                        if source.exists():
                            os.replace(source, Path(f"{self.path}.{index + 1}"))
                    if self.path.exists():
                        os.replace(self.path, Path(f"{self.path}.1"))
                else:
                    self.path.unlink(missing_ok=True)
            with self.path.open("ab") as output:
                output.write(line)
        return True
