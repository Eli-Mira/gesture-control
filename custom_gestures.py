"""Declarative, inert custom gesture profiles; independent of camera and OS."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from collections.abc import Mapping, Iterable

from .contracts import ActionIntent

SCHEMA_VERSION = 1
SAFE_ACTIONS = frozenset({"tab_next", "tab_previous", "zoom_in", "zoom_out",
                          "volume_up", "volume_down"})

# Initial rehearsal template; direction/thresholds are provisional until human calibration.
PORTAL_TEMPLATE = {"version": 1, "profiles": [{
    "name": "portal", "cooldown": 1.0, "steps": [
        {"pose": "open_palm", "role": "paired", "motion": "circle_cw",
         "duration": 0.30, "timeout": 2.0},
        {"pose": "open_palm", "role": "paired", "motion": "hold",
         "duration": 0.25, "timeout": 1.0},
    ],
}]}


@dataclass(frozen=True, slots=True)
class Step:
    pose: str
    role: str
    motion: str
    duration: float
    timeout: float


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    steps: tuple[Step, ...]
    cooldown: float
    binding: str | None = None


def _keys(value: object, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("missing or unknown schema fields")
    return value


def _number(value: object, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError("invalid duration, timeout or cooldown")
    return value


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_profiles(data: dict | str) -> tuple[Profile, ...]:
    """Validate a v1 JSON document/dict; no files, imports or executable fields."""
    if isinstance(data, str):
        if len(data) > 32_768:
            raise ValueError("profile document too large")
        try:
            data = json.loads(data, object_pairs_hook=_unique_pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
        except (TypeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("invalid JSON") from exc
    root = _keys(data, {"version", "profiles"})
    if type(root["version"]) is not int or root["version"] != SCHEMA_VERSION:
        raise ValueError("unsupported profile version")
    entries = root["profiles"]
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("at most 16 profiles")
    profiles = []
    names = set()
    for raw in entries:
        p = _keys(raw, {"name", "steps", "cooldown"}, {"binding"})
        name = p["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", name):
            raise ValueError("invalid profile name")
        if name.casefold() in names:
            raise ValueError("duplicate profile name")
        names.add(name.casefold())
        cooldown = _number(p["cooldown"], 0.2, 30.0)
        binding = p.get("binding")
        if binding is not None and (not isinstance(binding, str) or binding not in SAFE_ACTIONS):
            raise ValueError("binding must be an allowlisted inert action identifier")
        raw_steps = p["steps"]
        if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 8:
            raise ValueError("requires 1 to 8 steps")
        steps = []
        for raw_step in raw_steps:
            s = _keys(raw_step, {"pose", "role", "motion", "duration", "timeout"})
            if s["pose"] != "open_palm" or s["role"] != "paired" or s["motion"] not in ("circle_cw", "circle_ccw", "hold"):
                raise ValueError("only paired open-palm circle/hold is supported; no click/zoom/tab overlaps")
            duration = _number(s["duration"], 0.15, 3.0)
            timeout = _number(s["timeout"], duration, 5.0)
            steps.append(Step(s["pose"], s["role"], s["motion"], duration, timeout))
        if steps[0].motion == "hold" or any(s.motion != "hold" for s in steps[1:]):
            raise ValueError("sequence must begin with a circle, then only hold")
        profiles.append(Profile(name, tuple(steps), cooldown, binding))
    return tuple(profiles)


def dump_profiles(profiles: tuple[Profile, ...]) -> dict:
    """Return JSON-compatible data after revalidating even direct dataclass inputs."""
    result = []
    for p in profiles:
        entry = {"name": p.name, "cooldown": p.cooldown,
                 "steps": [{"pose": s.pose, "role": s.role, "motion": s.motion,
                            "duration": s.duration, "timeout": s.timeout} for s in p.steps]}
        if p.binding is not None:
            entry["binding"] = p.binding
        result.append(entry)
    document = {"version": SCHEMA_VERSION, "profiles": result}
    load_profiles(document)
    return document


def detect_conflicts(profile: Profile, active_modes: Iterable[str] = ()) -> tuple[str, ...]:
    """Conflicts must be arbitrated by caller; reject any occupied mode, even unknown ones."""
    issues = []
    if not profile.steps or profile.steps[0].motion not in ("circle_cw", "circle_ccw"):
        issues.append("sequence overlaps primary click or paired zoom/tabs")
    if any(s.pose != "open_palm" or s.role != "paired" for s in profile.steps):
        issues.append("primary click/drag pose or unsupported role")
    issues.extend(f"mode occupied: {mode}" for mode in sorted(set(active_modes)))
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class PoseSample:
    """Upstream-provided accepted hand pose, normalized palm center and stable track ID."""
    pose: str
    center: tuple[float, float]
    track_id: int


@dataclass(frozen=True, slots=True)
class GestureFeedback:
    cue: str
    progress: float
    reason: str
    intent: ActionIntent | None = None


class CustomRecognizer:
    """One selected profile, O(1) temporal state; no camera, execution or persistence."""

    def __init__(self, profile: Profile, *, enable_binding: bool = False,
                 approved: bool = False) -> None:
        load_profiles(dump_profiles((profile,)))
        self.profile = profile
        self.enable_binding = enable_binding is True
        # Approval is session-local; the schema cannot persist or forge it.
        self.approved = approved is True
        self.reset()

    def reset(self) -> None:
        self._stage = 0
        self._started = None
        self._step_started = None
        self._last_time = None
        self._angle = None
        self._hold_anchor = None
        self._turn = 0.0
        self._center = None
        self._span = None
        self._ids = None
        self._cool_until = float("-inf")
        self._needs_release = False

    def _clear_candidate(self) -> None:
        self._started = self._step_started = self._angle = self._hold_anchor = None
        self._turn = 0.0
        self._center = self._span = self._ids = None
        self._stage = 0

    def update(self, samples: Mapping[str, PoseSample], now: float, *,
               active_modes: Iterable[str] = (), cancel: bool = False,
               armed: bool = False) -> GestureFeedback:
        """One accepted frame. Caller supplies fresh role-labeled samples and arbiter modes.

        Result intents are inert proposals, never OS commands. `enable_binding`,
        session `approved`, and per-frame `armed` must all be explicit.
        """
        if type(now) not in (float, int) or not math.isfinite(now):
            raise ValueError("finite timestamp required")
        if self._last_time is not None and now < self._last_time:
            self.reset()
            return GestureFeedback("cancel", 0.0, "clock moved backward")
        gap = None if self._last_time is None else now - self._last_time
        self._last_time = now
        if self._started is not None and gap is not None and (gap == 0 or gap > .2):
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "stale or repeated frame")
        if cancel or detect_conflicts(self.profile, active_modes):
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "cancelled or primary/paired mode occupied")
        hands = (samples.get("primary"), samples.get("secondary"))
        valid = all(isinstance(h, PoseSample) and h.pose == "open_palm"
                    and type(h.track_id) is int and h.track_id >= 0
                    and isinstance(h.center, tuple) and len(h.center) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1
                            for v in h.center) for h in hands)
        if not valid:
            had_candidate = self._started is not None
            self._clear_candidate()
            if not any(hands):
                self._needs_release = False
            return GestureFeedback("cancel" if had_candidate else "idle", 0.0,
                                   "missing or invalid paired open palms")
        primary, secondary = hands
        ids = (primary.track_id, secondary.track_id)
        if ids[0] == ids[1]:
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "duplicate track ID")
        if now < self._cool_until:
            return GestureFeedback("cooldown", 0.0, "wait for cooldown")
        if self._needs_release:
            return GestureFeedback("release_to_rearm", 0.0, "remove both hands to retry")
        dx = primary.center[0] - secondary.center[0]
        dy = primary.center[1] - secondary.center[1]
        angle = math.atan2(dy, dx)
        span = math.hypot(dx, dy)
        center = ((primary.center[0] + secondary.center[0]) / 2,
                  (primary.center[1] + secondary.center[1]) / 2)
        if not .12 <= span <= .7:
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "paired span outside range")
        if self._started is None:
            self._started = self._step_started = now
            self._angle, self._center, self._span, self._ids = angle, center, span, ids
            return GestureFeedback("start", 0.0, "circle both open palms, then hold")
        if ids != self._ids:
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "hand role/track changed")
        step = self.profile.steps[self._stage]
        if now - self._step_started > step.timeout:
            self._clear_candidate()
            return GestureFeedback("timeout", 0.0, "step timed out; retry")
        if math.dist(center, self._center) > .08 or abs(span - self._span) > self._span * .20:
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "paired translation or zoom; retry")
        delta = math.atan2(math.sin(angle - self._angle), math.cos(angle - self._angle))
        self._angle = angle
        if abs(delta) > math.pi / 3:
            self._clear_candidate()
            return GestureFeedback("cancel", 0.0, "jump/identity ambiguity")
        if step.motion != "hold":
            self._turn += delta * (1 if step.motion == "circle_cw" else -1)
            fraction = max(0.0, min(1.0, self._turn / (math.tau * .85)))
            if fraction < 1 or now - self._step_started < step.duration:
                return GestureFeedback("circle", (self._stage + fraction) / len(self.profile.steps),
                                       "continue circle")
        else:
            drift = math.atan2(math.sin(angle - self._hold_anchor),
                               math.cos(angle - self._hold_anchor))
            if abs(drift) > .12:
                self._step_started = now
                self._hold_anchor = angle
                return GestureFeedback("hold", self._stage / len(self.profile.steps), "keep palms still")
            if now - self._step_started < step.duration:
                fraction = (now - self._step_started) / step.duration
                return GestureFeedback("hold", (self._stage + fraction) / len(self.profile.steps),
                                       "hold steady")
        self._stage += 1
        if self._stage == len(self.profile.steps):
            self._clear_candidate()
            self._cool_until = now + self.profile.cooldown
            self._needs_release = True
            intent = (ActionIntent(source=f"custom:{self.profile.name}",
                                   action=self.profile.binding, armed=True, timestamp=now)
                      if self.enable_binding and self.approved and armed is True
                      and self.profile.binding else None)
            return GestureFeedback("finish", 1.0, "completed; " +
                                   ("bound action proposed" if intent else "preview only"), intent)
        self._step_started = now
        self._hold_anchor = angle
        self._center, self._span = center, span
        return GestureFeedback("hold", self._stage / len(self.profile.steps), "circle complete; hold steady")
