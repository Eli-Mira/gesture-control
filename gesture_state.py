"""Gesture state machine for pinch-to-mouse control.

The state machine is deliberately free of platform/OS code (no ctypes, no
pyautogui) so it can be unit-tested deterministically. It consumes one
"observation" per camera frame and emits intent commands. A thin driver
(MouseDriver) turns those intents into real mouse actions.

Design goals:
  * Pinch = hold left button (drag). Release releases the button safely.
  * A short pinch "tap" = left click (no conflict with drag — duration-based).
  * Middle-finger-to-thumb pinch = right click (distinct anatomy).
  * Hysteresis/debounce prevents accidental drag starts, repeated clicks,
    and stuck buttons.
  * Any "no hand" / loss condition forces a safe release of every button.
"""

from __future__ import annotations

import enum
import time
from typing import Any


class State(enum.Enum):
    """Mouse state machine states."""
    IDLE = "idle"          # no hand, or open hand; nothing held
    TRACKING = "tracking"  # hand present, cursor active, no button
    DRAGGING = "dragging"  # pinch held, left button down
    RIGHT_HOLD = "right_hold"  # middle-pinch held, right button down
    SCROLLING = "scrolling"   # ring-pinch held; move to scroll


class PinchState(enum.Enum):
    """Distinguish which finger is pinching the thumb."""
    NONE = "none"          # no pinch
    PINCH = "pinch"        # thumb-to-index pinch
    MIDDLE = "middle"      # thumb-to-middle pinch
    RING = "ring"          # thumb-to-ring pinch (scroll)


class FrameObservation:
    """Normalized data for a single hand frame (all values 0..1 or None)."""

    __slots__ = (
        "thumb_tip", "index_tip", "middle_tip", "ring_tip",
        "hand_size", "thumb_index_dist", "thumb_middle_dist", "thumb_ring_dist",
    )

    def __init__(
        self,
        thumb_tip: tuple[float, float] | None = None,
        index_tip: tuple[float, float] | None = None,
        middle_tip: tuple[float, float] | None = None,
        ring_tip: tuple[float, float] | None = None,
        hand_size: float | None = None,
        thumb_index_dist: float | None = None,
        thumb_middle_dist: float | None = None,
        thumb_ring_dist: float | None = None,
    ) -> None:
        self.thumb_tip = thumb_tip          # (x, y) normalized
        self.index_tip = index_tip          # (x, y) normalized
        self.middle_tip = middle_tip        # (x, y) normalized
        self.ring_tip = ring_tip            # (x, y) normalized
        self.hand_size = hand_size          # scalar, normalized
        self.thumb_index_dist = thumb_index_dist   # thumb-tip to index-tip
        self.thumb_middle_dist = thumb_middle_dist # thumb-tip to middle-tip
        self.thumb_ring_dist = thumb_ring_dist     # thumb-tip to ring-tip


class GestureStateMachine:
    """Turns frame observations into mouse intents via hysteresis."""

    def __init__(
        self,
        pinch_threshold: float = 0.40,        # pinch_ratio below this = index pinch
        middle_pinch_threshold: float = 0.40, # ratio below this = middle pinch
        ring_pinch_threshold: float = 0.40,   # ratio below this = ring pinch (scroll)
        enter_delay: float = 0.08,            # seconds pinch must persist to start drag
        exit_delay: float = 0.08,             # seconds pinch absent before releasing
        tap_max_duration: float = 0.32,       # pinch shorter than this = click
        tap_move_tolerance: float = 0.06,     # max cursor travel (norm) for a tap
        click_cooldown: float = 0.35,         # min seconds between discrete clicks
        right_hold_delay: float = 0.12,       # seconds middle-pinch must persist
        scroll_sensitivity: float = 1.0,      # scroll speed multiplier
        scroll_dead_zone: float = 0.03,       # min vertical move (norm) to trigger scroll
        scroll_step: int = 1,               # wheel "clicks" per scroll step
    ) -> None:
        self.pinch_threshold = pinch_threshold
        self.middle_pinch_threshold = middle_pinch_threshold
        self.ring_pinch_threshold = ring_pinch_threshold
        self.enter_delay = enter_delay
        self.exit_delay = exit_delay
        self.tap_max_duration = tap_max_duration
        self.tap_move_tolerance = tap_move_tolerance
        self.click_cooldown = click_cooldown
        self.right_hold_delay = right_hold_delay
        self.scroll_sensitivity = scroll_sensitivity
        self.scroll_dead_zone = scroll_dead_zone
        self.scroll_step = scroll_step

        # Gesture enable/disable (checked in handlers).
        self.enable_drag = True
        self.enable_left_click = True
        self.enable_right_click = True
        self.enable_scroll = True

        self.state = State.IDLE

        # Left pinch tracking.
        self._pinch_start_time: float | None = None
        self._pinch_start_pos: tuple[float, float] | None = None
        self._pinch_end_time: float | None = None
        self._pinch_move_sum: float = 0.0
        self._pinch_last_pos: tuple[float, float] | None = None
        self._left_down = False

        # Middle pinch tracking.
        self._middle_start_time: float | None = None
        self._right_down = False

        # Ring pinch / scroll tracking.
        self._ring_start_time: float | None = None
        self._ring_last_y: float | None = None
        self._ring_accum: float = 0.0
        self._last_scroll_time = 0.0
        self._scroll_cooldown = 0.05

        # Cooldowns.
        self._last_left_click = 0.0
        self._last_right_click = 0.0

    # -- classification ---------------------------------------------------

    def _pinch_state(self, obs: FrameObservation) -> PinchState:
        """Classify which pinch, if any, is active in this observation."""
        hs = obs.hand_size if obs.hand_size else None
        denom_norm = hs if hs and hs > 0 else None

        def _ratio(dist: float | None) -> float:
            return (dist / denom_norm) if denom_norm and dist is not None else (dist if dist is not None else 1.0)

        # Ring pinch (scroll): thumb close to ring tip, index + middle clearly
        # separated (not themselves pinching).
        if obs.thumb_ring_dist is not None:
            ratio_ring = _ratio(obs.thumb_ring_dist)
            index_separated = True
            middle_separated = True
            if obs.thumb_index_dist is not None:
                index_separated = _ratio(obs.thumb_index_dist) >= self.pinch_threshold
            if obs.thumb_middle_dist is not None:
                middle_separated = _ratio(obs.thumb_middle_dist) >= self.middle_pinch_threshold
            if ratio_ring < self.ring_pinch_threshold and index_separated and middle_separated:
                return PinchState.RING

        # Middle pinch: thumb close to middle tip. To disambiguate from a
        # normal index pinch, require the index tip to be clearly separated
        # from the thumb (index NOT pinching).
        if obs.thumb_middle_dist is not None:
            ratio_mid = _ratio(obs.thumb_middle_dist)
            index_separated = True
            if obs.thumb_index_dist is not None:
                index_separated = _ratio(obs.thumb_index_dist) >= self.pinch_threshold
            if ratio_mid < self.middle_pinch_threshold and index_separated:
                return PinchState.MIDDLE

        if obs.thumb_index_dist is not None:
            ratio = _ratio(obs.thumb_index_dist)
            if ratio < self.pinch_threshold:
                return PinchState.PINCH

        return PinchState.NONE

    # -- output -----------------------------------------------------------

    def update(self, obs: FrameObservation | None, now: float | None = None) -> dict[str, Any]:
        """Advance one frame; return an intent dict.

        Keys:
          "move": (x, y) | None      -- normalized cursor target
          "left_down", "left_up", "left_click"
          "right_down", "right_up", "right_click"
          "state": str
          "action": str
          "scroll": int
        """
        if now is None:
            now = time.monotonic()

        result: dict[str, Any] = {
            "move": None,
            "left_down": False,
            "left_up": False,
            "left_click": False,
            "right_down": False,
            "right_up": False,
            "right_click": False,
            "scroll": 0,            # signed wheel steps this frame
            "state": self.state.value,
            "action": "",           # human-readable action label
        }

        if obs is None or obs.index_tip is None:
            self._force_release(result)
            result["state"] = self.state.value
            result["action"] = self._action_label(result)
            return result

        pinch = self._pinch_state(obs)
        result["move"] = obs.index_tip

        if pinch == PinchState.RING:
            self._on_ring(result, obs, now)
        elif pinch == PinchState.MIDDLE:
            self._on_middle(result, obs, now)
        elif pinch == PinchState.PINCH:
            self._on_pinch(result, obs, now)
        else:
            self._on_none(result, obs, now)

        result["state"] = self.state.value
        result["action"] = self._action_label(result)
        return result

    # -- handlers ---------------------------------------------------------

    def _on_pinch(self, result: dict[str, Any], obs: FrameObservation, now: float) -> None:
        """Handle index-thumb pinch (left drag / tap)."""
        if self._pinch_start_time is None:
            self._pinch_start_time = now
            self._pinch_start_pos = obs.index_tip
            self._pinch_last_pos = obs.index_tip
            self._pinch_move_sum = 0.0
        self._pinch_end_time = None

        # Accumulate cursor movement during the pinch (for tap disambiguation).
        if obs.index_tip is not None and self._pinch_last_pos is not None:
            dx = obs.index_tip[0] - self._pinch_last_pos[0]
            dy = obs.index_tip[1] - self._pinch_last_pos[1]
            self._pinch_move_sum += (dx * dx + dy * dy) ** 0.5
            self._pinch_last_pos = obs.index_tip

        # Cancel any pending middle/ring gesture (index pinch wins).
        self._release_right(result)
        self._middle_start_time = None
        self._reset_ring()

        if not self._left_down and self.enable_drag:
            held = now - self._pinch_start_time
            if held >= self.enter_delay:
                self._left_down = True
                self.state = State.DRAGGING
                result["left_down"] = True

    def _on_ring(self, result: dict[str, Any], obs: FrameObservation, now: float) -> None:
        """Ring pinch = scroll mode. Vertical hand movement scrolls."""
        self._release_left_and_reset(result)
        if self.state == State.RIGHT_HOLD or self._right_down:
            result["right_up"] = True
            self._right_down = False
        self._middle_start_time = None  # ring pinch cancels a pending right tap

        if not self.enable_scroll:
            return

        if self._ring_start_time is None:
            self._ring_start_time = now
            self._ring_last_y = obs.index_tip[1] if obs.index_tip else None
            self._ring_accum = 0.0
            self.state = State.SCROLLING
            return

        self.state = State.SCROLLING

        # Vertical movement of the index tip drives scroll.
        if obs.index_tip is not None and self._ring_last_y is not None:
            dy = obs.index_tip[1] - self._ring_last_y
            # Accumulate movement; only emit when past dead zone.
            self._ring_accum += dy
            self._ring_last_y = obs.index_tip[1]

            # One wheel notch per calibrated distance. Preserve fractional
            # travel and travel accumulated during the cooldown.
            distance_per_step = self.scroll_dead_zone / max(self.scroll_sensitivity, 0.01)
            if (abs(self._ring_accum) >= distance_per_step
                    and now - self._last_scroll_time >= self._scroll_cooldown):
                direction = 1 if self._ring_accum < 0 else -1
                steps = min(5, int(abs(self._ring_accum) / distance_per_step))
                result["scroll"] = direction * steps * self.scroll_step
                self._last_scroll_time = now
                self._ring_accum += direction * steps * distance_per_step

    def _on_middle(self, result: dict[str, Any], obs: FrameObservation, now: float) -> None:
        """Middle pinch = right click (hold then release)."""
        # A middle pinch supersedes any left drag/tap. Release left first so
        # the left button can never remain stuck while we switch to right.
        if self._left_down:
            result["left_up"] = True
            self._left_down = False
            if self.state == State.DRAGGING:
                self.state = State.TRACKING
        self._reset_pinch()
        self._reset_ring()

        if self._middle_start_time is None:
            self._middle_start_time = now

        if not self.enable_right_click:
            return

        held = now - self._middle_start_time
        if not self._right_down and held >= self.right_hold_delay:
            self._right_down = True
            self.state = State.RIGHT_HOLD
            result["right_down"] = True

    def _on_none(self, result: dict[str, Any], obs: FrameObservation, now: float) -> None:
        """No pinch: release buttons or complete pending clicks."""
        # Ring/scroll release path.
        if self.state == State.SCROLLING or self._ring_start_time is not None:
            self._reset_ring()

        # Right release path: a held button already makes one OS click on up.
        # Only a quick pinch that never pressed the button needs a synthetic click.
        if self._middle_start_time is not None:
            duration = now - self._middle_start_time
            if self._right_down:
                self._release_right(result)
            elif (
                self.enable_right_click
                and duration <= self.tap_max_duration
                and now - self._last_right_click >= self.click_cooldown
            ):
                result["right_click"] = True
                self._last_right_click = now
            self._middle_start_time = None
            self.state = State.TRACKING

        # Left release path.
        if self._left_down:
            # Record end-of-pinch time for hysteresis.
            if self._pinch_end_time is None:
                self._pinch_end_time = now
            if now - self._pinch_end_time >= self.exit_delay:
                self._release_left(result, now)
        elif self._pinch_start_time is not None:
            # Pinch was touching but never became a drag (sub-enter-duration).
            # Detect a short tap.
            if self._pinch_end_time is None:
                self._pinch_end_time = now
            if now - self._pinch_end_time >= self.exit_delay:
                self._release_left(result, now)

        if self.state == State.IDLE:
            pass
        elif self.state == State.DRAGGING and not self._left_down:
            self.state = State.TRACKING

    # -- low-level helpers ------------------------------------------------

    def _release_left(self, result: dict[str, Any], now: float) -> None:
        """Release left button and possibly emit a click."""
        duration = 0.0
        if self._pinch_start_time is not None and self._pinch_end_time is not None:
            duration = self._pinch_end_time - self._pinch_start_time
        moved = self._pinch_move_sum

        if (
            not self._left_down
            and self.enable_left_click
            and duration <= self.tap_max_duration
            and moved <= self.tap_move_tolerance
            and now - self._last_left_click >= self.click_cooldown
        ):
            result["left_click"] = True
            self._last_left_click = now

        if self._left_down:
            result["left_up"] = True
            self._left_down = False

        self._reset_pinch()
        if self.state == State.DRAGGING:
            self.state = State.TRACKING

    def _release_right(self, result: dict[str, Any]) -> None:
        """Release right button if held."""
        if self._right_down:
            result["right_up"] = True
            self._right_down = False
            if self.state == State.RIGHT_HOLD:
                self.state = State.TRACKING

    def _reset_pinch(self) -> None:
        """Reset all left-pinch tracking state."""
        self._pinch_start_time = None
        self._pinch_start_pos = None
        self._pinch_end_time = None
        self._pinch_move_sum = 0.0
        self._pinch_last_pos = None

    def _reset_ring(self) -> None:
        """Reset scroll/ring tracking state."""
        self._ring_start_time = None
        self._ring_last_y = None
        self._ring_accum = 0.0
        if self.state == State.SCROLLING:
            self.state = State.TRACKING

    def _release_left_and_reset(self, result: dict[str, Any]) -> None:
        """Release left button and reset pinch state."""
        if self._left_down:
            result["left_up"] = True
            self._left_down = False
        self._reset_pinch()
        if self.state == State.DRAGGING:
            self.state = State.TRACKING

    def _action_label(self, result: dict[str, Any]) -> str:
        """Human-readable label describing what just happened / current state."""
        if result.get("left_down"):
            return "Dragging"
        if result.get("left_click"):
            return "Left Click"
        if result.get("right_click"):
            return "Right Click"
        if result.get("right_down"):
            return "Right Hold"
        if result.get("scroll"):
            return "Scroll " + ("Up" if result["scroll"] > 0 else "Down")
        # No transient this frame; describe steady-state.
        state = self.state
        if state == State.DRAGGING:
            return "Dragging"
        if state == State.RIGHT_HOLD:
            return "Right Hold"
        if state == State.SCROLLING:
            return "Scroll"
        if state == State.IDLE:
            return "Tracking Lost"
        return "Moving"

    def _force_release(self, result: dict[str, Any]) -> None:
        """Force-release every button and reset state (used on hand loss)."""
        if self._left_down:
            result["left_up"] = True
            self._left_down = False
        if self._right_down:
            result["right_up"] = True
            self._right_down = False
        self.state = State.IDLE
        self._reset_pinch()
        self._reset_ring()
        self._middle_start_time = None

    def force_release(self, now: float | None = None) -> dict[str, Any]:
        """Force a safe release of all buttons and return the resulting intent."""
        if now is None:
            now = time.monotonic()
        result: dict[str, Any] = {
            "move": None, "left_down": False, "left_up": False,
            "left_click": False, "right_down": False, "right_up": False,
            "right_click": False, "scroll": 0, "state": self.state.value,
            "action": "",
        }
        self._force_release(result)
        result["state"] = self.state.value
        result["action"] = self._action_label(result)
        return result


# Alias kept for external callers that import StateMachine.
StateMachine = GestureStateMachine
