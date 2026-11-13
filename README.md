# Gesture Control

Real-time hand tracking and gesture recognition system for desktop control. Built with MediaPipe and OpenCV.

## Features

- 21 hand landmarks tracked at 30+ FPS
- Custom gesture definitions (swipe, pinch, point, fist, etc.)
- Multi-app targeting (switch between windows)
- Low-latency cursor control
- Extensible gesture/action mapping

## Requirements

- Python 3.11+
- Webcam
- Windows (primary) / Linux

## Install

```bash
# Create venv
python -m venv .venv
.venv\Scripts\activate

# Install deps
pip install -r requirements-gesture.txt
```

## Run

```bash
# Main gesture control app
python main.py

# Or use Windows launcher
launch_gesture_preview.bat
```

## Architecture

```
gesture-control/
├── engine.py              # Core tracking pipeline
├── features.py            # Landmark feature extraction
├── gestures.py            # Gesture definitions
├── actions.py             # Action execution (mouse, keys, etc.)
├── contracts.py           # Type contracts / interfaces
├── main.py                # Entry point
└── requirements-gesture.txt
```

## Configuration

Edit `settings.py` to customize:
- Gesture sensitivity thresholds
- Target application mappings
- Camera device index
- Smoothing/interpolation params

## License

MIT License — see [LICENSE](LICENSE)
