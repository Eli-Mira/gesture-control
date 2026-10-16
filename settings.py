"""Settings persistence for gesture control app."""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from .custom_gestures import PORTAL_TEMPLATE, dump_profiles, load_profiles

_HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_HERE, "..", "settings.json")
SCHEMA_VERSION = 2
MIGRATION_NOTICE = ("Legacy swipe mappings were preserved under legacy_gesture_map "
                    "but disabled in the new profile. Review and remap explicitly; "
                    "the original settings file is backed up before Save.")

VALID_GESTURES = (
    "none",
    "fist",
    "open_palm",
    "thumbs_up",
    "pinch",
    "swipe_left",
    "swipe_right",
    "swipe_up",
    "swipe_down",
)

GESTURE_OPTIONS = VALID_GESTURES

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "legacy_gesture_map": {},
    "migration_notice": "",
    # Only discrete (swipe/pose) gestures are routed here. Mouse click/drag is
    # handled by the dedicated state machine (see gesture_state.py), so it must
    # NOT appear in gesture_map.
    "gesture_map": {
        "volume_up": "none",
        "volume_down": "none",
        "window_prev": "none",
        "window_next": "none",
    },
    "wake_phrase": "start gestures",
    "stop_phrase": "stop gestures",
    "cooldown_seconds": 0.4,
    "volume_step_percent": 5,
    # --- mouse tuning ---
    "mouse_smoothing": 0.35,
    "mouse_dead_zone": 0.05,
    "mouse_sensitivity": 1.0,
    "mouse_pinch_threshold": 0.40,
    "mouse_middle_pinch_threshold": 0.40,
    "mouse_ring_pinch_threshold": 0.40,
    "mouse_drag_enter_delay": 0.08,
    "mouse_drag_exit_delay": 0.08,
    "mouse_click_cooldown": 0.35,
    "mouse_scroll_sensitivity": 1.0,
    "mouse_scroll_dead_zone": 0.03,
    # --- camera ---
    "camera_index": 0,
    # --- camera display ---
    "camera_mode": "normal",        # "normal" | "fullscreen"
    "camera_always_on_top": True,
    # New installations start in simulation until the operator enables input.
    "control_enabled": False,
    # --- gesture enable/disable (mouse control primitives) ---
    # New profiles require an explicit opt-in before a pinch holds a button.
    # Existing saved enable_drag=True remains respected by _sanitize().
    "enable_drag": False,
    "enable_left_click": True,
    "enable_right_click": True,
    "enable_scroll": True,
    # Preview/calibrate pointing before requiring its provisional pose in live input.
    "require_pointing_pose": False,
    "dual_hand": False,
    "paired_actions": False,
    "custom_preview": False,
    "custom_profile": PORTAL_TEMPLATE,
}

# Range limits for numeric mouse tuning fields: (min, max, default).
_MOUSE_FLOAT_FIELDS: dict[str, tuple[float, float, float]] = {
    "mouse_smoothing": (0.0, 1.0, 0.35),
    "mouse_dead_zone": (0.0, 0.4, 0.05),
    "mouse_sensitivity": (0.1, 5.0, 1.0),
    "mouse_pinch_threshold": (0.1, 1.0, 0.40),
    "mouse_middle_pinch_threshold": (0.1, 1.0, 0.40),
    "mouse_ring_pinch_threshold": (0.1, 1.0, 0.40),
    "mouse_drag_enter_delay": (0.02, 0.5, 0.08),
    "mouse_drag_exit_delay": (0.02, 0.5, 0.08),
    "mouse_click_cooldown": (0.05, 2.0, 0.35),
    "mouse_scroll_sensitivity": (0.1, 5.0, 1.0),
    "mouse_scroll_dead_zone": (0.005, 0.2, 0.03),
}

# Boolean toggle fields (parsed from raw truthy/falsy values).
_BOOL_FIELDS = (
    "camera_always_on_top",
    "control_enabled",
    "enable_drag",
    "enable_left_click",
    "enable_right_click",
    "enable_scroll",
    "require_pointing_pose",
    "dual_hand",
    "paired_actions",
    "custom_preview",
)

# Enum-like string fields: (name, allowed set, default).
_STR_FIELDS = (
    ("camera_mode", ("normal", "fullscreen"), "normal"),
)

_WRITE_LOCK = threading.Lock()


def _sanitize(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge raw user settings onto defaults, coercing and clamping each field."""
    clean = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(raw.get("legacy_gesture_map"), dict):
        clean["legacy_gesture_map"] = json.loads(json.dumps(raw["legacy_gesture_map"]))
    if "custom_profile" in raw:
        try:
            clean["custom_profile"] = dump_profiles(load_profiles(raw["custom_profile"]))
            if len(clean["custom_profile"]["profiles"]) != 1:
                clean["custom_profile"] = json.loads(json.dumps(PORTAL_TEMPLATE))
        except (ValueError, TypeError, OverflowError):
            clean["custom_profile"] = json.loads(json.dumps(PORTAL_TEMPLATE))

    gesture_map = raw.get("gesture_map")
    if isinstance(gesture_map, dict):
        for action in list(clean["gesture_map"]):
            value = str(gesture_map.get(action, "")).strip().lower()
            if value in VALID_GESTURES:
                clean["gesture_map"][action] = value

    for key in ("wake_phrase", "stop_phrase"):
        phrase = str(raw.get(key, "")).strip().lower()
        if phrase:
            clean[key] = phrase

    try:
        cooldown = float(raw.get("cooldown_seconds", DEFAULT_SETTINGS["cooldown_seconds"]))
        clean["cooldown_seconds"] = min(5.0, max(0.05, cooldown))
    except (TypeError, ValueError):
        pass

    try:
        step = int(raw.get("volume_step_percent", DEFAULT_SETTINGS["volume_step_percent"]))
        clean["volume_step_percent"] = min(25, max(1, step))
    except (TypeError, ValueError):
        pass

    for field, (lo, hi, default) in _MOUSE_FLOAT_FIELDS.items():
        try:
            value = float(raw.get(field, default))
            clean[field] = min(hi, max(lo, value))
        except (TypeError, ValueError):
            clean[field] = default

    for field in _BOOL_FIELDS:
        raw_val = raw.get(field, clean[field])
        if isinstance(raw_val, str):
            clean[field] = raw_val.strip().lower() in ("1", "true", "yes", "on")
        else:
            clean[field] = bool(raw_val)

    for field, allowed, default in _STR_FIELDS:
        value = str(raw.get(field, default)).strip().lower()
        clean[field] = value if value in allowed else default

    try:
        camera_index = int(raw.get("camera_index", DEFAULT_SETTINGS["camera_index"]))
        if 0 <= camera_index <= 4:
            clean["camera_index"] = camera_index
    except (TypeError, ValueError):
        pass

    return clean


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Stage legacy mappings without enabling old swipe-driven OS actions."""
    if raw.get("schema_version") == SCHEMA_VERSION:
        return _sanitize(raw)
    if "schema_version" in raw:
        raise ValueError("Unsupported gesture settings schema version")
    migrated = dict(raw)
    if isinstance(raw.get("gesture_map"), dict):
        migrated["legacy_gesture_map"] = raw["gesture_map"]
    migrated["gesture_map"] = dict(DEFAULT_SETTINGS["gesture_map"])
    migrated["schema_version"] = SCHEMA_VERSION
    clean = _sanitize(migrated)
    clean["migration_notice"] = MIGRATION_NOTICE
    return clean


def settings_backup_path() -> str:
    return SETTINGS_PATH + ".pre-v2.bak"


def load_settings() -> dict[str, Any]:
    """Load settings.json, falling back to defaults per-field on any problem."""
    raw: dict[str, Any] = {}
    read_error = ""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            raw = data
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        read_error = f"Invalid settings file ({exc}); original file untouched. Review before Save."
        print(f"[settings] could not read {SETTINGS_PATH} ({exc}) — using defaults.")
    try:
        clean = _migrate(raw) if raw else _sanitize(raw)
        if read_error:
            clean["migration_notice"] = read_error
        return clean
    except ValueError as exc:
        print(f"[settings] {exc} — using disabled defaults; original untouched.")
        clean = _sanitize({})
        clean["migration_notice"] = f"{exc}; original file untouched. Do not Save without reviewing it."
        return clean


def save_settings(settings: dict[str, Any]) -> None:
    """Validate and atomically write settings.json after a byte-exact backup."""
    if "schema_version" in settings and settings["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported gesture settings schema version")
    clean = _migrate(settings) if settings and "schema_version" not in settings else _sanitize(settings)
    clean["control_enabled"] = False  # Save never persists arming across launches.
    clean["migration_notice"] = ""
    with _WRITE_LOCK:
        backup = settings_backup_path()
        if os.path.isfile(SETTINGS_PATH) and not os.path.exists(backup):
            created = False
            try:
                with open(SETTINGS_PATH, "rb") as source, open(backup, "xb") as dest:
                    created = True
                    while chunk := source.read(1024 * 1024):
                        dest.write(chunk)
                    dest.flush()
                    os.fsync(dest.fileno())
            except Exception:
                if created:
                    os.unlink(backup)
                raise
        tmp_path = SETTINGS_PATH + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, indent=2)
            os.replace(tmp_path, SETTINGS_PATH)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def restore_settings_backup() -> None:
    """Restore exact pre-v2 bytes only on explicit operator request."""
    with _WRITE_LOCK:
        tmp_path = SETTINGS_PATH + ".rollback.tmp"
        try:
            with open(settings_backup_path(), "rb") as source, open(tmp_path, "wb") as dest:
                while chunk := source.read(1024 * 1024):
                    dest.write(chunk)
            os.replace(tmp_path, SETTINGS_PATH)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
