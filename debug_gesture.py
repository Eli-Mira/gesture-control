#!/usr/bin/env python3
import os, re, json
from pathlib import Path

target = r"C:\Projects\CODING\CODINGWITHMIRA\gesture_control"
skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".tox", ".hg"}
source_exts = {".py"}

files = []
for root, dirs, filenames in os.walk(target):
    dirs[:] = [d for d in dirs if d not in skip]
    for fn in filenames:
        if Path(fn).suffix in source_exts:
            files.append(str(Path(root) / fn))
files.sort()

tracking_files = [f for f in files if 'track' in f.lower() or 'gesture' in f.lower() or 'hand' in f.lower()]

print("=" * 70)
print("FULL DEBUGGING SESSION: GESTURE TRACKING")
print("=" * 70)
print(f"Project: {target}")
print(f"Files: {len(files)}")
print(f"Tracking files: {len(tracking_files)}")
print()

for fpath in tracking_files:
    print(f"Analyzing: {os.path.basename(fpath)}")
    try:
        content = Path(fpath).read_text(errors='ignore')
        lines = content.split('\n')
        
        patterns = {
            'MediaPipe': 'MediaPipe integration',
            'OpenCV': 'OpenCV usage', 
            'landmark': 'Landmark detection',
            'hand_landmarks': 'Hand landmarks',
            'confidence': 'Confidence threshold',
            'tracking': 'Tracking logic',
            'process': 'Processing function',
            'result': 'Result handling',
        }
        
        found = {}
        for pattern, desc in patterns.items():
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                found[desc] = len(matches)
        
        if found:
            print(f"  Patterns found: {found}")
        else:
            print(f"  No key tracking patterns found")
        
        issues = []
        if 'if not results:' in content or 'if not results' in content:
            issues.append("Empty results check")
        if 'if results.multi_hand_landmarks:' in content:
            issues.append("Multi-hand check")
        if 'results.multi_hand_landmarks' in content:
            issues.append("MediaPipe results structure")
        if 'results.world_landmarks' in content:
            issues.append("World landmarks")
        if 'detection_confidence' in content or 'min_detection_confidence' in content:
            issues.append("Detection confidence")
        if 'tracking_confidence' in content or 'min_tracking_confidence' in content:
            issues.append("Tracking confidence")
        
        if issues:
            print(f"  Potential issues: {issues}")
        
        print(f"  First 20 lines:")
        for line in lines[:20]:
            print(f"    {line}")
        print()
    except Exception as e:
        print(f"  Error: {e}")
        print()

print("MAIN TRACKING FLOW ANALYSIS")
print("=" * 70)

engine = Path(target, "engine.py").read_text(errors='ignore')
ui = Path(target, "ui.py").read_text(errors='ignore')

setup_patterns = [
    ('mp_holistic', 'Holistic model'),
    ('mp_hands', 'Hands model'), 
    ('mp_pose', 'Pose model'),
    ('with mp.', 'Context manager'),
    ('static_image_mode', 'Image mode flag'),
    ('min_detection_confidence', 'Detection confidence'),
    ('min_tracking_confidence', 'Tracking confidence'),
]

print("MediaPipe setup analysis:")
for pattern, desc in setup_patterns:
    if pattern in engine or pattern in ui:
        print(f"  ✅ {desc}: Present")
    else:
        print(f"  ❌ {desc}: Missing")

print("\nTracking logic analysis:")
tracking_keywords = ['for hand', 'for hand_landmarks', 'if results', 'process', 'update']
found_keywords = [kw for kw in tracking_keywords if kw.lower() in (engine+ui).lower()]
print(f"  Tracking-related keywords found: {found_keywords}")

landmark_patterns = ['landmarks', 'landmark', 'coordinates', 'position']
found_landmark = [kw for kw in landmark_patterns if kw.lower() in (engine+ui).lower()]
print(f"Landmark-related keywords: {found_landmark}")

fps_indicators = ['fps', 'clock', 'tick', 'waitKey', 'delay']
found_fps = [kw for kw in fps_indicators if kw.lower() in (engine+ui).lower()]
print(f"FPS/performance indicators: {found_fps}")

print()
print("DEBUGGING RECOMMENDATIONS")
print("=" * 70)

recommendations = []
if 'min_detection_confidence' not in (engine+ui):
    recommendations.append("⚠️ Add min_detection_confidence (default 0.5)")

if 'min_tracking_confidence' not in (engine+ui):
    recommendations.append("⚠️ Add min_tracking_confidence (default 0.5)")

if 'if not results:' not in (engine+ui) and 'if results.multi_hand_landmarks' not in (engine+ui):
    recommendations.append("⚠️ Add proper results empty check")

if 'mp_hands' not in (engine+ui) and 'mp_holistic' not in (engine+ui):
    recommendations.append("⚠️ Initialize MediaPipe Hands model")

if 'fps' not in (engine+ui) and 'clock' not in (engine+ui):
    recommendations.append("⚠️ Add FPS counter for performance monitoring")

if 'cv2.imshow' not in (engine+ui) and 'cv2.waitKey' not in (engine+ui):
    recommendations.append("⚠️ Add cv2.imshow/waitKey for visualization")

if recommendations:
    for r in recommendations:
        print(r)
else:
    print("✅ No obvious configuration gaps found")