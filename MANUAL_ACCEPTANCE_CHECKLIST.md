# Gesture Control Major Update — one-session supervised acceptance

**Status:** Development and automated verification are complete for the preview-first implementation. **No physical OS-input, live dual-hand, brow-only, or end-to-end release acceptance has been signed off.** These boxes are for Eli to test in one sitting, not claims of passing. In the project root, the latest automated run used `-o addopts=''`: **400 passed, 1 skipped, 3 unrelated pytest warnings**; compileall, targeted Ruff, and the model integrity check returned 0. A public-sample FPS proxy is not perceived cursor latency. Movement and response polish stays open until your feedback.

## Before starting — safety and setup

- [ ] Close any other camera users. Keep one monitor on the camera panel and an empty text document/browser test tab as the only initial OS-input target. Do **not** run an unattended physical-input test.
- [ ] Open `C:\Projects\CODING\CODINGWITHMIRA\launch_gesture_preview.bat` in Explorer. It verifies the bundled model first and launches **disarmed**. If model verification fails, stop rather than bypassing it. The normal saved `settings.json` is **not** rewritten on launch.
- [ ] Confirm the UI says **PREVIEW ONLY // NO OS INPUT**. `Ctrl+Shift+F12` is emergency Pause (test with focus outside the app); the visible Pause button is the fallback. **Pause after each live action group**; a camera loss, focus change, or input error should also fail closed. Stop and report any cursor/click/volume/shortcut while disarmed.
- [ ] In this existing profile, explicitly turn **Drag OFF** before live click tests and press Save while paused: the saved file has `enable_drag=true` from before this update, although **new** profiles default it off. Leave `Pointing pose required` off for the baseline; test it separately below. The four legacy swipe mappings are already `none`.
- [ ] Record a short note for each failure: phase, exact pose and lighting, whether Preview or Live, mode/rejection/input-error text, intended vs actual action, and whether `Ctrl+Shift+F12` released control. No face video, screenshots, or diagnostics are uploaded or saved by default.

## Phase 0 — rollback and emergency stop

- [ ] With no OS input armed, show one hand and verify the **Enable Control** button only becomes available after tracking. Arm deliberately, then press `Ctrl+Shift+F12` while another app has focus: the UI must return to Preview and stop actions. Restart: still disarmed even if a previous save said enabled.
- [ ] Do **not** perform a rollback now. Confirm `dev corner/gesture_control_phase0_rollback.md` and the pre-update settings backup exist; only use them if the test session must be abandoned.

## Phase 1 — detector/performance baseline (preview only)

- [ ] With one then two hands visible in ordinary lighting, note the displayed FPS and whether the camera/overlay follows without a noticeable stall. Try the actual camera and lighting you intend to use, not just a public sample.
- [ ] Empty scene and face/brows **without any hand entering frame** for a few seconds: record `detected`, `accepted`, and rejection reason. The earlier `brow`-named trace contained hands and does **not** establish this negative case. No video recording is needed.

## Phase 2 — snapshot integrity and safe disarm

- [ ] Preview with one hand, then remove it suddenly: skeleton and action cue should clear; no stale pointer or pinch action should persist. Pause while a button is held during a later live drag test: button-up must happen immediately. Only the first item is preview-only.

## Phase 3 — false-positive gate / camera loss

- [ ] Preview the clean brow-only, empty-background, and hands-near-face cases. Face/background must not become an **accepted hand**; a genuine hand in front of the face should not be blanket-rejected. Note detection versus acceptance separately.
- [ ] With the app paused, cover/unplug the camera, use **Retry Camera**, and restore it. No old skeleton/tips should emit a new action or silently arm control. If a live test later loses tracking, it must release input immediately.
- [ ] Only after the above preview checks, briefly arm on a blank safe target and repeat deliberate hand-in/hand-out. **Zero unintended OS moves or clicks**; Pause at once on any anomaly. This is the missing human gate, not covered by fake-driver replay.

## Phase 4 — primary pointer / click / optional pointing / drag

- [ ] Live on the safe test target: move one primary hand slowly and quickly, then hold an open fist/neutral pose. Note responsiveness, jitter, dead zone, overshoot, and whether movement stops on loss. The experimental **Pointing pose required** toggle is OFF by default; switch it ON in Preview, test stable index-point and non-pointing suppression, then decide whether it is usable for Live.
- [ ] With Drag OFF, perform **thumb–index pinch then release** for one left click, and **thumb–middle pinch then release** for one right click. Each deliberate gesture should yield exactly one correct click, no button stuck, no click from a held pinch, and no click while disarmed. Note failures before changing a threshold.
- [ ] Only after those pass, explicitly opt into Drag, hold/move/release on disposable content, then repeat while pressing Pause and while losing the hand. Button-up must always occur. Turn Drag OFF again if uncertain.

## Phase 5 — stable dual-hand roles

- [ ] While paused, enable **Dual hand**, Save, and let the camera reacquire. Preview should show **green primary**, **amber secondary**, numeric role IDs, and an understandable mode/rejection cue. One hand should remain usable when the second is absent.
- [ ] Add/remove the second hand, change camera mirroring/order, slowly cross hands, briefly occlude one, and show hands near the face. It must not silently transfer primary control or preserve a held button. Use **USE SECONDARY AS PRIMARY** only when both stable tracks are visible; the switch should disarm one transition frame and release any held input.

## Phase 6 — secondary volume (one guarded live group)

- [ ] Enable **Paired actions** while paused. In Preview, release to a neutral pose **after** both hands acquire (a modifier held through startup is deliberately ignored). Hold the secondary modifier and move the secondary hand **vertically**; the proposal should name volume up/down. Ordinary movement, jitter, one missing hand, and a primary click pose should not change system volume.
- [ ] With volume initially low and on a safe audio target, arm briefly, try one upward and one downward deliberate step, then Pause. Verify the **actual Windows output level**, bounded behavior, and how the UI reports errors. Try a muted endpoint: it must not surprise-unmute. Do not turn volume up loudly to test limits.

## Phase 7 — paired zoom and browser tabs / focus binding

- [ ] Preview two open hands: move apart/together for zoom proposals; move together sideways at roughly constant span for tab proposals. They must not trigger one another, click, or activate on one-hand loss. Reset to neutral between attempts.
- [ ] Open a disposable tab in **Chrome, Edge, or Firefox** (VS Code is also profiled). Focus that exact window and press **Ctrl+Shift+F11** to bind it. The NAVI panel itself and an unlisted app must not bind. With Paired actions ON, arm briefly and verify a deliberate paired zoom changes only zoom and a sideways motion changes only the tab. Legacy `Alt+Tab` switches **Windows**, not tabs, and remains disabled by default.
- [ ] Change focus to a different window, then try the same pose: no shortcut should fire until you explicitly rebind the new supported target. Pause and verify no held mouse input remains. Do not test by firing shortcuts into important unsaved work.

## Phase 8 — custom cinematic sequence (preview only)

- [ ] Turn **Custom preview** ON while paused. Rehearse the portal-like paired circle/hold and watch progress/cancel/retry cues. Missing hand, timeout, wrong direction, or a click/zoom/tab conflict should cancel or defer the sequence; there must be no OS effect.
- [ ] Use **EXPORT CUSTOM PROFILE** to a disposable JSON file, optionally edit its declarative pose/motion parameters, then **IMPORT CUSTOM PROFILE** and Save. A malformed/oversized file must be rejected with a clear error. A `binding` field remains **inert even while control is armed**; live custom binding is withheld pending positive/negative replay and your explicit supervised approval.

## Phase 9 — UI, settings migration, legibility

- [ ] Confirm hand IDs, two-color skeletons, mode/proposal/candidate progress, preview/live badge, rejection reason, input errors, camera retry, Pause, help text, and one-screen checklist remain legible in both normal and fullscreen views. `Esc` should exit fullscreen without secretly arming or closing an active input path.
- [ ] Save while paused and restart: preview/disarmed state, selected camera, toggles, and a validated custom profile should survive; saving must not arm the pointer. Check the migration notice and **Restore previous file** only if you deliberately test rollback with a separate copied settings file; your original mappings must not silently disappear.

## Phase 10 — release/hardening sign-off

- [ ] From the project root, `.venv\Scripts\python.exe -m gesture_control.release --check-only` exits 0 with the bundled model, and the Explorer launcher starts Preview. Do not replace `hand_landmarker.task` just to test a mismatch; automated tests cover missing/tampered files.
- [ ] Run several normal launch/close cycles plus camera disconnect/retry, a second monitor if you use one, and one emergency Pause while another app has focus. Watch for stuck buttons, unwanted OS actions, crashes, lost camera handles, and CPU/FPS/response regressions. Diagnostic export is opt-in **numeric only** via `--diagnostics-dir`; do not enable it for ordinary testing. Delete the earlier temporary numeric trace if you no longer need it.
- [ ] Mark a phase **pass** only after its live-dependent boxes pass. If any box fails, keep the release gate open and send the phase number, scenario, actual/expected result, and panel telemetry. Python remains the implementation language unless a measured bottleneck demonstrates otherwise.
