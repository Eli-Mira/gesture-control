"""Tkinter NAVI console for the gesture control daemon.

Serial Experiments Lain // WIRED aesthetic: void-black terminal, phosphor
green telemetry, amber signal readouts. The live camera feed is embedded at
the top as an Eyego viewport so the operator always sees what the seer sees.

Public entry for the settings panel:
    launch_settings_ui(get_listening, on_closed=None,
                       frame_provider=None, status_provider=None,
                       stop_event=None)
This call runs Tk on the calling thread (the application main thread). The
camera preview runs separately, and stop_event closes the panel safely.
"""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
import threading
from tkinter import filedialog, messagebox, ttk

# Support direct execution (python ui.py) by falling back to absolute imports.
try:
    from .custom_gestures import dump_profiles, load_profiles
    from .settings import (load_settings, save_settings, settings_backup_path,
                           restore_settings_backup, GESTURE_OPTIONS)
    from .lain_theme import (
        AMBER, DIM, ENTRY, FONT_BUTTON, FONT_DATA, FONT_DISPLAY,
        FONT_LABEL, FONT_LABEL_BOLD, GHOST, MUTED, PANEL,
        PANEL_EDGE, PHOSPHOR, RED, SUBTITLE, TICKER, VOID,
    )
except ImportError:
    # Direct execution fallback.
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    from gesture_control.custom_gestures import dump_profiles, load_profiles  # noqa: E402
    from gesture_control.settings import (load_settings, save_settings,  # noqa: E402
                                          settings_backup_path, restore_settings_backup,
                                          GESTURE_OPTIONS)
    from gesture_control.lain_theme import (  # noqa: E402
        AMBER, DIM, ENTRY, FONT_BUTTON, FONT_DATA, FONT_DISPLAY,
        FONT_LABEL, FONT_LABEL_BOLD, GHOST, MUTED, PANEL,
        PANEL_EDGE, PHOSPHOR, RED, SUBTITLE, TICKER, VOID,
    )

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except ImportError:  # pragma: no cover - degraded mode without Pillow
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    _PIL_OK = False

_ACTIONS = [
    ("volume_up", "Volume up"),
    ("volume_down", "Volume down"),
    ("window_prev", "Window previous"),
    ("window_next", "Window next"),
]

_MOUSE_TUNING = [
    ("mouse_smoothing", "Smoothing", 0.0, 1.0),
    ("mouse_dead_zone", "Dead zone", 0.0, 0.4),
    ("mouse_sensitivity", "Sensitivity", 0.1, 5.0),
    ("mouse_pinch_threshold", "Pinch threshold", 0.1, 1.0),
    ("mouse_middle_pinch_threshold", "Middle-pinch threshold", 0.1, 1.0),
    ("mouse_ring_pinch_threshold", "Ring-pinch threshold", 0.1, 1.0),
    ("mouse_drag_enter_delay", "Drag enter delay (s)", 0.02, 0.5),
    ("mouse_drag_exit_delay", "Drag exit delay (s)", 0.02, 0.5),
    ("mouse_click_cooldown", "Click cooldown (s)", 0.05, 2.0),
    ("mouse_scroll_sensitivity", "Scroll sensitivity", 0.1, 5.0),
    ("mouse_scroll_dead_zone", "Scroll dead zone", 0.005, 0.2),
]

_CAMERA_MODES = ["normal", "fullscreen"]
_CAMERA_INDEXES = [0, 1, 2, 3, 4]

_BOOL_OPTIONS = [
    ("enable_drag", "Enable drag (pinch-hold)"),
    ("enable_left_click", "Enable left click (tap)"),
    ("enable_right_click", "Enable right click (middle pinch)"),
    ("enable_scroll", "Enable scroll (ring pinch)"),
    ("require_pointing_pose", "Require index-point pose (experimental)"),
    ("dual_hand", "Dual-hand tracking (preview opt-in)"),
    ("paired_actions", "Paired actions (experimental opt-in)"),
    ("custom_preview", "Custom gesture preview (no OS action)"),
    ("camera_always_on_top", "Camera always on top"),
]

_VIEW_W, _VIEW_H = 384, 384


class ToggleSwitch(tk.Frame):
    """Phosphor on/off toggle switch widget."""

    def __init__(self, master: tk.Misc, label: str, default: bool = True,
                 command: any = None, width: int = 36, **kwargs: any) -> None:
        super().__init__(master, bg=PANEL, **kwargs)
        self._value = default
        self._command = command
        self._width = width
        self._canvas = tk.Canvas(self, width=width + 8, height=20,
                                 bg=PANEL, highlightthickness=0, bd=0)
        self._canvas.pack(side="left", padx=(0, 8))
        self._label_var = tk.StringVar(value=label)
        self._label = tk.Label(self, textvariable=self._label_var,
                               fg=GHOST, bg=PANEL, anchor="w", justify="left",
                               font=FONT_LABEL)
        self._label.pack(side="left", fill="both", expand=True)
        self._canvas.bind("<Button-1>", self._toggle)
        self._label.bind("<Button-1>", self._toggle)
        self._draw()

    def _toggle(self, _event: any = None) -> None:
        self._value = not self._value
        self._draw()
        if self._command:
            self._command(self._value)

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        x1, x2, y1, y2 = 0, self._width, 2, 18
        fill = PHOSPHOR if self._value else "#2a3530"
        canvas.create_rectangle(x1, y1, x2, y2, fill="", outline=DIM, width=1)
        knob_x = x2 - 10 if self._value else x1 + 2
        canvas.create_rectangle(knob_x, y1 + 1, knob_x + 8, y2 - 1,
                                fill=fill, outline=fill, width=0)

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._draw()


def _build_group(parent: tk.Misc, title: str) -> tk.Frame:
    frame = tk.Frame(parent, bg=PANEL, padx=12, pady=8,
                     highlightbackground=PANEL_EDGE, highlightthickness=1)
    header = tk.Label(frame, text=f"// {title}",
                      fg=PHOSPHOR, bg=PANEL, anchor="w", font=FONT_LABEL_BOLD)
    header.pack(fill="x", pady=(0, 2))
    sep = tk.Frame(frame, height=1, bg=PANEL_EDGE)
    sep.pack(fill="x", pady=(0, 6))
    return frame


def _format_telemetry(status: dict) -> str:
    """Compact, honest tracking state; never infer confidence from depth."""
    sig = str(status.get("gesture") or "---").upper()
    action = str(status.get("action") or "IDLE").upper()
    tracking = str(status.get("tracking") or status.get("state") or "STARTING")
    try:
        fps = f"{float(status.get('fps') or 0):.0f}"
    except (TypeError, ValueError):
        fps = "--"
    camera = status.get("camera_index", "?")
    badge = "LIVE // OS INPUT" if status.get("control_enabled") else "PREVIEW // NO OS INPUT"
    text = f"{badge}\nSIG:{sig}  ACT:{action}  FPS:{fps}\nTRACK:{tracking}  cam{camera}"
    primary = status.get("primary_track_id")
    secondary = status.get("secondary_track_id")
    text += f"\nPRIMARY:{primary if primary is not None else '—'}  SECONDARY:{secondary if secondary is not None else '—'}"
    text += f"\nMODE:{status.get('mode') or 'pointer'}"
    if status.get("mode_reason"):
        text += f" ({status['mode_reason']})"
    if status.get("custom_cue"):
        text += f"\nCUSTOM:{status['custom_cue']}  progress:{status.get('custom_progress', 0)}"
    if status.get("rejection_reason") and status.get("hand_detected"):
        text += f"\nREJECT:{status['rejection_reason']}"
    if status.get("require_pointing_pose"):
        text += f"\nPOINTER:{status.get('pointing_reason') or 'waiting'}"
    error = status.get("input_error") or status.get("error")
    if error:
        text += f"\nERR:{error}"
    return text


def _gesture_help_text() -> str:
    return ("Primary: point to move; thumb-index pinch/release = left click; "
            "thumb-middle = right click. Drag is opt-in. Secondary held modifier + "
            "vertical motion = volume (opt-in). Two open hands: apart/together = "
            "zoom; lateral motion = browser tab (opt-in, app-specific). "
            "For zoom/tabs, focus a supported app and press Ctrl+Shift+F11 to bind "
            "that window; focus changes revoke binding. "
            "Custom sequence = preview only. Legacy Alt+Tab switches WINDOWS, "
            "not tabs; old swipe routes default off. Rehearse in preview first.")


def _operator_checklist_text() -> str:
    return ("1 Camera and hand accepted?  2 Preview pinch/roles/mode, no OS input.  "
            "3 Check error banner.  4 Enable explicitly; test one action.  "
            "5 Pause control or Ctrl+Shift+F12 on any surprise.")


def _configure_combobox_style(root: tk.Tk) -> None:
    """Keep readonly selections legible on the dark Navi entry field."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Navi.TCombobox", fieldbackground=ENTRY,
                    background=PANEL, foreground=GHOST,
                    selectbackground=DIM, selectforeground=GHOST,
                    arrowcolor=PHOSPHOR, borderwidth=0)
    # A readonly Combobox inherits clam's light fieldbackground via a state
    # map; configure() alone cannot override it.
    style.map("Navi.TCombobox",
              fieldbackground=[("readonly", ENTRY)],
              foreground=[("disabled", MUTED), ("readonly", GHOST)],
              selectbackground=[("readonly", DIM)],
              selectforeground=[("readonly", GHOST)])
    root.option_add("*TCombobox*Listbox.background", ENTRY)
    root.option_add("*TCombobox*Listbox.foreground", GHOST)
    root.option_add("*TCombobox*Listbox.selectBackground", DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", GHOST)


def launch_settings_ui(get_listening: any, on_closed: any = None,
                        frame_provider: any = None,
                        status_provider: any = None,
                        stop_event: threading.Event | None = None,
                        on_control_toggle: any = None,
                        on_tuning_change: any = None,
                        on_apply_settings: any = None,
                        on_camera_retry: any = None,
                        bundle_provider: any = None,
                        on_choose_primary: any = None) -> None:
    """Run Tk on the calling/main thread until closed or stop_event is set."""
    _ui_main(get_listening, on_closed, frame_provider, status_provider, stop_event,
             on_control_toggle, on_tuning_change, on_apply_settings, on_camera_retry,
             bundle_provider, on_choose_primary)


def _startup_ui_settings() -> dict:
    """Show preview at launch even if a legacy file remembers LIVE."""
    return {**load_settings(), "control_enabled": False}


def _ui_main(get_listening: any, on_closed: any,
              frame_provider: any, status_provider: any,
              stop_event: threading.Event | None,
              on_control_toggle: any, on_tuning_change: any,
              on_apply_settings: any, on_camera_retry: any,
              bundle_provider: any = None,
              on_choose_primary: any = None) -> None:
    settings = _startup_ui_settings()
    root = tk.Tk()
    root.title("NAVI // Gesture Daemon")
    root.geometry("440x920")
    root.minsize(400, 600)
    root.attributes("-topmost", True)
    root.configure(bg=VOID)

    # --- ttk dark/Navi styling for comboboxes ---
    _configure_combobox_style(root)

    # --- header: NAVI masthead + wired ticker ---
    header_frame = tk.Frame(root, bg=VOID)
    header_frame.pack(fill="x", padx=14, pady=(12, 0))
    title_label = tk.Label(header_frame, text="/// NAVI",
                           fg=PHOSPHOR, bg=VOID, font=FONT_DISPLAY)
    title_label.pack(side="left")
    sub_label = tk.Label(header_frame, text="GESTURE DAEMON",
                         fg=MUTED, bg=VOID, font=FONT_LABEL)
    sub_label.pack(side="left", padx=(8, 0), pady=(4, 0))
    subtitle = tk.Label(root, text=SUBTITLE, fg=DIM, bg=VOID, font=FONT_DATA)
    subtitle.pack(fill="x", padx=14)
    ticker = tk.Label(root, text=TICKER, fg=DIM, bg=VOID, font=FONT_DATA)
    ticker.pack(fill="x", padx=14, pady=(0, 6))

    # --- scrollable console body ---
    canvas = tk.Canvas(root, bg=VOID, highlightthickness=0, bd=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    container = tk.Frame(canvas, bg=VOID, padx=14, pady=6)
    canvas.create_window((0, 0), window=container, anchor="nw", tags="body")

    def _sync_scroll(_event: any = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig("body", width=canvas.winfo_width() - 4)

    container.bind("<Configure>", lambda _e: canvas.configure(
        scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", _sync_scroll)

    notice = settings.get("migration_notice")
    if notice:
        tk.Label(container, text=f"MIGRATION // {notice}", fg=AMBER, bg=VOID,
                 justify="left", anchor="w", wraplength=392,
                 font=FONT_DATA).pack(fill="x", pady=(0, 8))
    tk.Label(container, text="// MONITOR + OPERATE: " + _operator_checklist_text(),
             fg=GHOST, bg=VOID, justify="left", anchor="w", wraplength=392,
             font=FONT_DATA).pack(fill="x", pady=(0, 8))

    def _on_wheel(event: any) -> None:
        delta = getattr(event, "delta", 0)
        step = int(-delta / 120) if delta else (1 if event.num == 5 else -1)
        canvas.yview_scroll(step, "units")

    canvas.bind_all("<MouseWheel>", _on_wheel)
    canvas.bind_all("<Button-4>", _on_wheel)
    canvas.bind_all("<Button-5>", _on_wheel)

    # --- status / listening indicator ---
    status_frame = tk.Frame(container, bg=VOID)
    status_frame.pack(fill="x", pady=(0, 8))
    indicator = tk.Canvas(status_frame, width=14, height=14,
                          bg=VOID, highlightthickness=0)
    indicator.pack(side="left", padx=(0, 6))
    dot = indicator.create_oval(2, 2, 12, 12, fill=PHOSPHOR, outline="")
    state_label = tk.Label(status_frame, text="WIRED: ?",
                           fg=GHOST, bg=VOID, font=FONT_LABEL_BOLD)
    state_label.pack(side="left")

    # --- EYEGO viewport: embedded live camera ---
    view_shell = tk.Frame(container, bg=VOID,
                          highlightbackground=PHOSPHOR, highlightthickness=1)
    view_shell.pack(fill="x", pady=(0, 2))
    view_title = tk.Label(view_shell, text="// EYEGO FEED - WHAT THE SEER SEES",
                          fg=PHOSPHOR, bg=VOID, font=FONT_DATA, anchor="w")
    view_title.pack(fill="x", padx=6, pady=(4, 0))
    view_label = tk.Label(view_shell, bg="#000000", fg=MUTED,
                          font=FONT_LABEL, width=48, height=24,
                          text="// NO CARRIER\nwaiting for visual cortex...")
    view_label.pack(fill="x", padx=6, pady=6)
    telemetry_var = tk.StringVar(value="TRACK:STARTING")
    telemetry = tk.Label(view_shell, textvariable=telemetry_var,
                         fg=AMBER, bg=VOID, font=FONT_DATA, anchor="w",
                         justify="left", wraplength=392)
    telemetry.pack(fill="x", padx=6, pady=(0, 4))
    primary_label = tk.Label(view_shell, text="PRIMARY: —", fg=PHOSPHOR,
                             bg=VOID, font=FONT_DATA, anchor="w")
    primary_label.pack(fill="x", padx=6)
    secondary_label = tk.Label(view_shell, text="SECONDARY: —", fg=AMBER,
                               bg=VOID, font=FONT_DATA, anchor="w")
    secondary_label.pack(fill="x", padx=6)
    input_error_label = tk.Label(view_shell, text="", fg=RED, bg=VOID,
                                 font=FONT_LABEL_BOLD, anchor="w",
                                 justify="left", wraplength=392)
    input_error_label.pack(fill="x", padx=6)
    layer_note = tk.Label(container, text="god is in the inner layer // layer:07",
                          fg=DIM, bg=VOID, font=FONT_DATA, anchor="w")
    layer_note.pack(fill="x", pady=(0, 8))

    # First-run calibration stays safe: show gestures, inject nothing until
    # the operator has seen a tracked hand and explicitly enables control.
    calibration_group = _build_group(container, "CALIBRATION // SAFE PREVIEW")
    calibration_var = tk.StringVar(value="1 / Connect a camera.  2 / Show your hand.")
    tk.Label(calibration_group, textvariable=calibration_var, fg=GHOST, bg=PANEL,
             font=FONT_LABEL, anchor="w", justify="left", wraplength=375).pack(fill="x")
    mode_var = tk.StringVar(value="PREVIEW ONLY // NO OS INPUT")
    tk.Label(calibration_group, textvariable=mode_var, fg=AMBER, bg=PANEL,
             font=FONT_LABEL_BOLD, anchor="w").pack(fill="x", pady=(4, 4))
    calibration_buttons = tk.Frame(calibration_group, bg=PANEL)
    calibration_buttons.pack(fill="x")
    seen_hand = {"value": False}
    visible_roles = {"secondary": None}

    def _toggle_control() -> None:
        desired = not bool(settings.get("control_enabled", False))
        if on_control_toggle is None:
            return
        try:
            on_control_toggle(desired)
            settings["control_enabled"] = desired
        except Exception as exc:
            calibration_var.set(f"Could not change control mode: {exc}")

    control_btn = tk.Button(calibration_buttons, text="// ENABLE CONTROL",
                            command=_toggle_control, state="disabled",
                            bg=ENTRY, fg=PHOSPHOR, activebackground=DIM,
                            activeforeground=GHOST, relief="flat", font=FONT_BUTTON)
    control_btn.pack(side="left", padx=(0, 8))

    def _retry_camera() -> None:
        if on_camera_retry is None:
            return
        selected = camera_vars["camera_index"].get() if "camera_index" in camera_vars else str(settings["camera_index"])
        try:
            accepted = on_camera_retry(int(selected))
            if accepted:
                seen_hand["value"] = False
            calibration_var.set("Searching cameras..." if accepted else "Camera retry already running")
        except Exception as exc:
            calibration_var.set(f"Camera retry failed: {exc}")

    retry_btn = tk.Button(calibration_buttons, text="// RETRY CAMERA",
                          command=_retry_camera, state="normal" if on_camera_retry else "disabled",
                          bg=ENTRY, fg=GHOST, activebackground=DIM,
                          activeforeground=GHOST, relief="flat", font=FONT_BUTTON)
    retry_btn.pack(side="left")

    def _choose_secondary() -> None:
        track_id = visible_roles["secondary"]
        if on_choose_primary is None or type(track_id) is not int:
            return
        try:
            if on_choose_primary(track_id):
                calibration_var.set(f"Primary selection queued: track {track_id}; reacquiring")
                seen_hand["value"] = False
            else:
                calibration_var.set("Track changed; show both hands and try again")
        except Exception as exc:
            calibration_var.set(f"Role selection failed: {exc}")

    role_btn = tk.Button(calibration_group, text="// USE SECONDARY AS PRIMARY",
                         command=_choose_secondary, state="disabled",
                         bg=ENTRY, fg=AMBER, activebackground=DIM,
                         activeforeground=GHOST, relief="flat", font=FONT_DATA)
    role_btn.pack(fill="x", pady=(6, 0))
    tk.Label(calibration_group,
             text="EMERGENCY PAUSE: Ctrl+Shift+F12 (works outside this window)",
             fg=RED, bg=PANEL, font=FONT_DATA, anchor="w").pack(fill="x", pady=(6, 0))
    calibration_group.pack(fill="x", pady=(0, 8))

    def _refresh_view() -> None:
        """Pull one frame + status tick (~15fps) for the embedded viewport."""
        try:
            active = bool(get_listening())
            indicator.itemconfig(dot, fill=PHOSPHOR if active else RED)
            state_label.config(text=f"{'// WIRED: LISTENING' if active else '// WIRED: PAUSED'}",
                               fg=PHOSPHOR if active else RED)

            st, frame = {}, None
            if bundle_provider is not None:
                try:
                    st, frame = bundle_provider()
                except Exception as exc:
                    st = {"error": f"Camera/status provider failed: {exc}"}
            elif status_provider is not None:
                try:
                    st = status_provider() or {}
                except Exception as exc:
                    st = {"error": f"Status provider failed: {exc}"}
            telemetry_var.set(_format_telemetry(st))
            telemetry.config(fg=RED if st.get("error") or st.get("input_error") else AMBER)
            primary = st.get("primary_track_id")
            secondary = st.get("secondary_track_id")
            visible_roles["secondary"] = secondary
            role_btn.config(state="normal" if on_choose_primary and type(secondary) is int
                            and st.get("camera_ok") else "disabled")
            primary_label.config(text=f"PRIMARY: {primary if primary is not None else '—'}")
            secondary_label.config(text=f"SECONDARY: {secondary if secondary is not None else '—'}")
            input_error_label.config(text=f"INPUT ERROR: {st['input_error']}" if st.get("input_error") else "")
            live = bool(st.get("control_enabled", settings.get("control_enabled", False)))
            settings["control_enabled"] = live
            if st.get("hand_tracked"):
                seen_hand["value"] = True
            mode_var.set("LIVE CONTROL // OS INPUT ENABLED" if live
                         else "PREVIEW ONLY // NO OS INPUT")
            control_btn.config(text="// PAUSE CONTROL" if live else "// ENABLE CONTROL",
                               state="normal" if on_control_toggle and
                               (live or (seen_hand["value"] and st.get("camera_ok")))
                               else "disabled")
            if st.get("error") or st.get("input_error"):
                calibration_var.set(str(st.get("input_error") or st.get("error")))
            elif not st.get("camera_ok"):
                calibration_var.set("1 / Camera unavailable. Select an index or Retry Camera.")
            elif not seen_hand["value"]:
                calibration_var.set("2 / Show your hand to the camera. Controls remain safe.")
            elif not live:
                calibration_var.set(f"3 / Test pinch, drag and scroll here: {st.get('action') or 'move your hand'}. Then enable.")
            else:
                calibration_var.set(f"Gesture: {st.get('action') or 'tracking'} // Pause any time")

            if bundle_provider is None and frame_provider is not None:
                try:
                    frame = frame_provider()
                except Exception as exc:
                    telemetry_var.set(_format_telemetry({"error": f"Camera feed failed: {exc}"}))
                    telemetry.config(fg=RED)
            if frame is not None and _PIL_OK:
                import cv2  # lazy: only needed while a feed is live
                rgb = cv2.cvtColor(frame if bundle_provider is not None else frame.copy(),
                                   cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)
                img = img.resize((_VIEW_W, _VIEW_H), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                view_label.config(image=photo, text="")
                view_label.image = photo
            elif frame is None:
                view_label.config(image="", text="// NO CARRIER\nwaiting for visual cortex...")
                view_label.image = None
        except Exception as exc:
            print(f"[ui] refresh failed: {exc!r}")
            telemetry_var.set(f"ERR:Panel refresh failed: {exc}")
            telemetry.config(fg=RED)
        finally:
            try:
                if root.winfo_exists():
                    root.after(66, _refresh_view)
            except tk.TclError:
                pass  # panel was closed during shutdown

    # --- voice section ---
    def _apply_live(field: str, value: any) -> None:
        settings[field] = value
        if settings.get("control_enabled"):
            # The runtime's apply_settings resets pointer/hand state. Never
            # invoke it under a held button: Pause then Save applies edits.
            calibration_var.set("Change staged. Pause and Save to apply safely.")
            return
        if on_tuning_change is not None:
            try:
                on_tuning_change(field, value)
            except Exception as exc:
                calibration_var.set(f"Live setting failed: {exc}")

    voice_group = _build_group(container, "VOICE PHRASES")
    wake_var = tk.StringVar(value=settings["wake_phrase"])
    stop_var = tk.StringVar(value=settings["stop_phrase"])
    _add_entry_row(voice_group, "Wake phrase", wake_var)
    _add_entry_row(voice_group, "Stop phrase", stop_var)
    voice_group.pack(fill="x", pady=(0, 8))

    # --- gesture mapping section ---
    gesture_group = _build_group(container, "GESTURE MAP")
    tk.Label(gesture_group, text="Legacy volume/swipe and Alt+Tab WINDOW routes default off;\n"
             "paired browser TAB actions are separate and app-specific.",
             fg=AMBER, bg=PANEL, font=FONT_DATA, justify="left",
             anchor="w").pack(fill="x")
    combos: dict[str, tk.StringVar] = {}
    for action, label in _ACTIONS:
        _add_combo_row(gesture_group, label, settings["gesture_map"].get(action, "none"),
                       combos, action,
                       on_change=lambda value, a=action: _apply_live(
                           "gesture_map", {**settings["gesture_map"], a: value}))
    gesture_group.pack(fill="x", pady=(0, 8))

    help_group = _build_group(container, "GESTURE HELP // PREVIEW FIRST")
    tk.Label(help_group, text=_gesture_help_text(), fg=GHOST, bg=PANEL,
             font=FONT_DATA, wraplength=370, justify="left",
             anchor="w").pack(fill="x")
    help_group.pack(fill="x", pady=(0, 8))

    profile_group = _build_group(container, "CUSTOM SEQUENCE // PREVIEW ONLY")
    profile_name = tk.StringVar(value=settings.get("custom_profile", {})
                                .get("profiles", [{}])[0].get("name", "portal"))
    tk.Label(profile_group, textvariable=profile_name, fg=AMBER, bg=PANEL,
             font=FONT_DATA, anchor="w").pack(fill="x")
    tk.Label(profile_group,
             text="Import one validated JSON sequence; bindings stay inert. "
                  "Enable Custom preview, then Save to keep it.",
             fg=GHOST, bg=PANEL, font=FONT_DATA, wraplength=370,
             justify="left", anchor="w").pack(fill="x")

    def _import_profile() -> None:
        path = filedialog.askopenfilename(parent=root, title="Import custom sequence",
                                         filetypes=[("JSON profile", "*.json")])
        if not path:
            return
        try:
            if os.path.getsize(path) > 65536:
                raise ValueError("Profile exceeds the 64 KiB import limit")
            with open(path, "r", encoding="utf-8") as stream:
                profiles = load_profiles(json.load(stream))
            if len(profiles) != 1:
                raise ValueError("Import exactly one profile at a time")
            document = dump_profiles(profiles)
            _apply_live("custom_profile", document)
            profile_name.set(f"{profiles[0].name} // PREVIEW ONLY")
        except (ValueError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as exc:
            messagebox.showerror("Invalid custom sequence", str(exc), parent=root)

    def _export_profile() -> None:
        path = filedialog.asksaveasfilename(parent=root, title="Export custom sequence",
                                            defaultextension=".json",
                                            filetypes=[("JSON profile", "*.json")])
        if not path:
            return
        try:
            document = dump_profiles(load_profiles(settings["custom_profile"]))
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
        except (ValueError, TypeError, OSError) as exc:
            messagebox.showerror("Export failed", str(exc), parent=root)

    tk.Button(profile_group, text="// IMPORT CUSTOM PROFILE", command=_import_profile,
              bg=ENTRY, fg=AMBER, relief="flat", font=FONT_DATA).pack(fill="x", pady=2)
    tk.Button(profile_group, text="// EXPORT CUSTOM PROFILE", command=_export_profile,
              bg=ENTRY, fg=AMBER, relief="flat", font=FONT_DATA).pack(fill="x", pady=2)
    profile_group.pack(fill="x", pady=(0, 8))

    # --- mouse tuning section ---
    mouse_group = _build_group(container, "SEER CALIBRATION")
    mouse_vars: dict[str, tk.StringVar] = {}
    for field, label, lo, hi in _MOUSE_TUNING:
        _add_slider_row(mouse_group, label, settings.get(field, ""), mouse_vars, field, lo, hi,
                        on_change=lambda value, key=field: _apply_live(key, value))
    mouse_group.pack(fill="x", pady=(0, 8))

    # --- camera section ---
    camera_group = _build_group(container, "EYE (CAMERA)")
    camera_vars: dict[str, tk.StringVar] = {}
    _add_combo_row(camera_group, "Camera index", str(settings.get("camera_index", 0)),
                   camera_vars, "camera_index", values=[str(i) for i in _CAMERA_INDEXES],
                   on_change=lambda value: _apply_live("camera_index", int(value)))
    _add_combo_row(camera_group, "Camera mode", settings.get("camera_mode", "normal"),
                   camera_vars, "camera_mode", values=_CAMERA_MODES,
                   on_change=lambda value: _apply_live("camera_mode", value))
    camera_group.pack(fill="x", pady=(0, 8))

    # --- toggles section ---
    toggles_group = _build_group(container, "DAEMON RITES")
    toggle_vars: dict[str, ToggleSwitch] = {}
    for field, label in _BOOL_OPTIONS:
        toggle = ToggleSwitch(toggles_group, label, default=bool(settings.get(field, True)),
                              command=lambda value, key=field: _apply_live(key, value))
        toggle.pack(fill="x", pady=3)
        toggle_vars[field] = toggle
    toggles_group.pack(fill="x", pady=(0, 10))

    # --- action area ---
    action_frame = tk.Frame(container, bg=VOID)
    action_frame.pack(fill="x", pady=(0, 10))

    status = tk.StringVar(value="")
    status_label = tk.Label(action_frame, textvariable=status,
                            fg=GHOST, bg=VOID, font=FONT_DATA)
    status_label.pack(side="left", fill="x", expand=True)

    def save() -> None:
        if settings.get("control_enabled"):
            if on_control_toggle is None:
                messagebox.showerror("Pause before Save", "Pause control before changing settings.", parent=root)
                return
            try:
                # Release any held input before applying mappings/camera tuning.
                on_control_toggle(False)
                settings["control_enabled"] = False
            except Exception as exc:
                messagebox.showerror("Pause failed", str(exc), parent=root)
                return
        data: dict[str, any] = {
            **settings,
            "control_enabled": False,
            "gesture_map": {action: var.get() for action, var in combos.items()},
            "wake_phrase": wake_var.get(),
            "stop_phrase": stop_var.get(),
            "cooldown_seconds": settings["cooldown_seconds"],
            "volume_step_percent": settings["volume_step_percent"],
            "camera_mode": camera_vars["camera_mode"].get(),
            "camera_index": int(camera_vars["camera_index"].get()),
        }
        for field, _label, _lo, _hi in _MOUSE_TUNING:
            raw = mouse_vars[field].get().strip()
            try:
                value = float(raw)
                data[field] = max(_lo, min(_hi, value))
            except ValueError:
                data[field] = settings.get(field)
        for field, _label in _BOOL_OPTIONS:
            data[field] = bool(toggle_vars[field].get())
        try:
            save_settings(data)
            settings.update(load_settings())
            if on_apply_settings is not None:
                on_apply_settings(settings)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Save/apply failed", str(exc), parent=root)
            return
        status.set("// settings saved; tuning active now. Voice phrases apply next launch.")
        status_label.config(fg=PHOSPHOR)
        root.after(3000, lambda: status.set(""))

    def restore_previous() -> None:
        if settings.get("control_enabled"):
            messagebox.showerror("Pause before restore", "Pause control before restoring settings.", parent=root)
            return
        if not os.path.isfile(settings_backup_path()):
            messagebox.showerror("No rollback file", "Save once to create a rollback file.", parent=root)
            return
        if not messagebox.askyesno("Restore previous file",
                                   "Restore the original settings file? Restart NAVI to load it; "
                                   "old swipe mappings remain disabled on migration.", parent=root):
            return
        try:
            restore_settings_backup()
        except OSError as exc:
            messagebox.showerror("Restore failed", str(exc), parent=root)
            return
        status.set("// previous file restored. Restart to reload safely in preview.")
        status_label.config(fg=AMBER)

    tk.Button(action_frame, text="Restore previous file", command=restore_previous,
              bg=ENTRY, fg=AMBER, activebackground=DIM, relief="flat",
              font=FONT_DATA).pack(side="left", padx=(0, 4))

    save_btn = tk.Button(action_frame, text="// ETCH (SAVE)", command=save,
                         bg="#0d2818", fg=PHOSPHOR,
                         activebackground="#123722", activeforeground=GHOST,
                         highlightbackground=PHOSPHOR, highlightthickness=1,
                         relief="flat", padx=14, pady=6,
                         font=FONT_BUTTON, cursor="hand2")
    save_btn.pack(side="right", padx=(8, 0))

    footer = tk.Label(container, text="no matter where you are, everyone is always connected",
                      fg=DIM, bg=VOID, font=FONT_DATA)
    footer.pack(fill="x", pady=(0, 8))

    def _on_window_close() -> None:
        try:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        except Exception:
            pass
        if on_closed is not None:
            on_closed()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_window_close)

    def _close_when_stopped() -> None:
        if stop_event is not None and stop_event.is_set():
            _on_window_close()
        else:
            root.after(100, _close_when_stopped)

    if stop_event is not None:
        root.after(100, _close_when_stopped)
    root.after(200, _refresh_view)
    root.mainloop()


def _add_entry_row(parent: tk.Misc, label: str, var: tk.StringVar) -> None:
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", pady=3)
    lbl = tk.Label(row, text=label, fg=MUTED, bg=PANEL,
                   anchor="w", width=18, font=FONT_LABEL)
    lbl.pack(side="left")
    entry = tk.Entry(row, textvariable=var, bg=ENTRY, fg=GHOST,
                     relief="flat", insertbackground=PHOSPHOR,
                     highlightbackground=PANEL_EDGE, highlightthickness=1,
                     font=FONT_LABEL, width=24)
    entry.pack(side="left", padx=(8, 0), pady=2)


def _add_combo_row(parent: tk.Misc, label: str, default: str,
                   combos: dict[str, tk.StringVar], key: str,
                   values: list[str] | None = None,
                   on_change: any = None) -> None:
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", pady=3)
    lbl = tk.Label(row, text=label, fg=MUTED, bg=PANEL,
                   anchor="w", width=18, font=FONT_LABEL)
    lbl.pack(side="left")
    if values is None:
        values = list(GESTURE_OPTIONS)
    var = tk.StringVar(value=default)
    combos[key] = var
    combo = ttk.Combobox(row, textvariable=var, values=values,
                         state="readonly", width=14, style="Navi.TCombobox")
    if on_change is not None:
        combo.bind("<<ComboboxSelected>>", lambda _event: on_change(var.get()))
    combo.pack(side="left", padx=(8, 0), pady=2)


def _add_slider_row(parent: tk.Misc, label: str, default: str,
                    vars_map: dict[str, tk.StringVar], key: str,
                    lo: float, hi: float, on_change: any = None) -> None:
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", pady=2)
    lbl = tk.Label(row, text=label, fg=MUTED, bg=PANEL,
                   anchor="w", width=22, font=FONT_LABEL)
    lbl.pack(side="left")
    var = tk.StringVar(value=str(default))
    vars_map[key] = var

    scale = tk.Scale(row, from_=lo, to=hi, resolution=max(0.001, (hi - lo) / 200.0),
                     orient="horizontal", variable=var, bg=PANEL,
                     fg=PHOSPHOR, troughcolor=ENTRY, highlightthickness=0,
                     bd=0, sliderrelief="flat", length=150,
                     activebackground=PHOSPHOR,
                     command=lambda v: (var.set(v), on_change(v) if on_change else None))
    scale.pack(side="left", padx=(8, 0), pady=2)


# Debug visualization
def draw_landmarks_on_image(rgb_image, hand_landmarks):
    """Draw hand landmarks on the image for debugging."""
    annotated_image = rgb_image.copy()
    # Draw the hand landmarks
    mp_drawing = mp.solutions.drawing_utils
    mp_drawing.draw_landmarks(
        annotated_image, hand_landmarks, mp_hands.HAND_CONNECTIONS,
        mp_drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2),
        mp_drawing.DrawingSpec(color=(0, 0, 255), thickness=2, circle_radius=2)
    )
    return annotated_image

# In the main loop, add visualization
if results and results.multi_hand_landmarks:
    for hand_landmarks in results.multi_hand_landmarks:
        frame = draw_landmarks_on_image(rgb_image, hand_landmarks)
        cv2.imshow('Gesture Control - Debug', frame)
        cv2.waitKey(1) & 0xFF
