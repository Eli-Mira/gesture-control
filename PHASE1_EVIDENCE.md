# Phase 1 evidence — replay and two-hand feasibility

**Scope:** preview-only diagnosis. No Windows input was injected, and no raw frames from Eli's camera were stored or uploaded. Public sample images and derived crops were temporary under the Hermes scratch directory and may be pruned. This report preserves the numeric evidence and a reproducible recipe.

## What was tested

1. **Public image fixtures:** Downloaded `woman_hands.jpg` from the official MediaPipe hand-landmarker sample storage (`https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg`). The photograph visibly has two hands and a face. Tested it at original 640×960 and as a letterboxed 480×480 image. Derived a face/eyebrow crop `(215, 450, 430, 610)` resized to 480×480, plus two single-hand crops, a partially occluded-hand variant, a fabric-only crop, and an empty black frame. All public-sample image derivatives stayed in scratch, not in the repository.
2. **Installed model:** `hand_landmarker.task`, MediaPipe 1.0.1, the app's thresholds (detection 0.6, presence 0.5, tracking 0.5), IMAGE mode. With `num_hands=1`, the full sample produced one hand; with `num_hands=2`, it produced two, with `hand_landmarks` and `handedness` lists aligned. The 480×480 letterboxed sample also produced two hands in two-hand mode. The face/eyebrow crop, fabric-only region, and black frame each produced **zero** hands in this controlled test; each single-hand crop and the partial-occlusion variant produced one in two-hand mode. **This does not reproduce Eli's brow false-positive.** A human-specific clip or numeric landmark trace is still needed to identify its cause; don't claim the false-positive is fixed.
3. **Real camera benchmark on Mira:** sequential VIDEO-mode runs with `num_hands=1` and `2`, each 1.5 s warm-up then about 8 s collection, camera 0, 480×480 request, mirrored frame, preview-only overlay. Mode 1: **29.7 rendered FPS**, p95 frame cycle **48.3 ms**, p95 from `cap.read()` return to `imshow`/`waitKey` return **22.4 ms** (proxy), 238 frames. Mode 2: **29.6 rendered FPS**, p95 frame cycle **47.7 ms**, same pipeline proxy **19.1 ms**, 237 frames. Two hands were returned in 156 of 237 frames during mode 2. The scene and Eli's hand poses were **not controlled** between sequential runs; do not interpret the count difference as a model-quality comparison. The proxy excludes camera sensor and compositor latency; end-to-end photons-to-display latency remains unmeasured.
4. **Synthetic trace replay:** `gesture_control/replay.py` accepts timestamped `{"t": seconds, "hand": {normalized tips/distances}}` JSONL and outputs *simulated* gesture intents; it imports no OS driver. `tests/test_gesture_replay.py` proves a synthetic short index pinch yields one left click, a short middle pinch yields one right click, held button release on loss occurs once, and unordered timestamps are rejected. `tests/fixtures/gesture_two_hand_crossing.jsonl` is a **synthetic future-role fixture**, not real tracking evidence.

## Diagnosis and limits

- (user) Eyebrows were visually identified as a hand and clicks seemed missing. Those exact incidents were not captured; the public eyebrow crop returned no detection and the synthetic clean pinches produced click intents. **Do not claim either symptom is reproduced or fixed.**
- (verified: `engine.py:191-199,248-295`) Current production engine is still `num_hands=1` and uses only `hand_landmarks[0]`. The bench did **not** change the running detector.
- (verified: `gesture_state.py:226-375`, replay tests) A held left/right button can produce an OS click on button-up without emitting the explicit `left_click`/`right_click` intent label. This may explain an apparent missing-click indicator but does not prove why Eli experienced a failed physical click. Need a supervised clip/trace with hand-size-normalized thumb distances and timing to separate classification, routing, and OS-injection failures.
- The sampled 480×480 two-hand fixture plus live camera FPS meet the provisional *pipeline* go/no-go target (≥15 rendered FPS, p95 capture-return-to-display proxy ≤250 ms). True physical end-to-end latency remains a later manual check. Future role tracking must still handle hands crossing/occlusion.

## Reproduce / use the evidence

```text
.venv/Scripts/python.exe -m pytest -q -o addopts='' tests/test_gesture_replay.py
.venv/Scripts/python.exe -m gesture_control.replay path/to/landmark_trace.jsonl
```

A trace line is JSON with `t` (strictly increasing seconds) and `hand` (`null` when absent or an object with any of `thumb_tip`, `index_tip`, `middle_tip`, `ring_tip`, `hand_size`, `thumb_index_dist`, `thumb_middle_dist`, `thumb_ring_dist`). The replay emits only intents and `simulation: true`. `tests/test_gesture_replay.py` supplies a minimal working sequence. Do not label a synthetic negative as a real eyebrow case.

The live camera numeric benchmark was saved to scratch as `gesture_phase1_camera_benchmark.json` (may be pruned); this report is durable. Before recording Eli's actual face or hand video, ask permission. Prefer opt-in numeric landmark traces first; store them locally and include a deletion path.

**Next:** Phase 2 creates immutable multi-hand frame observations with a single timestamp and adapters, without switching the production detector to two hands yet. Phase 3 builds acceptance/rejection and will need the real brow trace to validate false-positive handling. Phase 4 revisits the real click path with user-supervised calibration.
