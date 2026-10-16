"""Fail-closed model integrity gate for the gesture-only release launcher.

The digest is of the 7,819,105-byte hand_landmarker.task shipped in the project
root. A model replacement requires an explicit reviewed manifest update.
"""
from __future__ import annotations

import hashlib
import argparse
import importlib
import sys
from pathlib import Path
from threading import Thread

MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "hand_landmarker.task"


class ModelIntegrityError(RuntimeError):
    """The app must not start when the bundled model cannot be trusted."""


def verify_model(model_path: Path | None = None) -> Path:
    """Return the validated model path, or fail before camera/UI startup."""
    path = Path(model_path) if model_path is not None else DEFAULT_MODEL
    try:
        with path.open("rb") as model:
            digest = hashlib.file_digest(model, "sha256").hexdigest()
    except FileNotFoundError as exc:
        raise ModelIntegrityError(f"model missing: {path}") from exc
    except OSError as exc:
        raise ModelIntegrityError(f"model unreadable: {path}") from exc
    if digest != MODEL_SHA256:
        raise ModelIntegrityError(f"model SHA-256 mismatch: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    """Verify the fixed bundled model before importing the camera application."""
    parser = argparse.ArgumentParser(description="Gesture preview integrity gate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--preview-only", action="store_true")
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="explicitly enable local numeric-only rotating diagnostic export")
    args = parser.parse_args(argv)
    try:
        verify_model()
    except ModelIntegrityError as exc:
        print(f"Gesture release blocked: {exc}", file=sys.stderr)
        return 2
    if args.check_only:
        return 0
    # GestureApp starts disarmed even if persisted settings were once armed.
    app_module = importlib.import_module("gesture_control.main")
    if args.diagnostics_dir is None:
        app_module.main()
        return 0

    from .diagnostics import DiagnosticLog
    original_app = app_module.GestureApp
    output = DiagnosticLog(args.diagnostics_dir, enabled=True)

    class OptInDiagnosticsApp(original_app):
        def __init__(self, *app_args, **app_kwargs):
            super().__init__(*app_args, **app_kwargs)

            def export_loop() -> None:
                while not self.stop_event.is_set():
                    try:
                        output.record(self.engine.get_snapshot())
                    except OSError:
                        print("Gesture diagnostics stopped: local export failed", file=sys.stderr)
                        return
                    self.stop_event.wait(0.25)

            Thread(target=export_loop, name="numeric-diagnostics", daemon=True).start()

    app_module.GestureApp = OptInDiagnosticsApp
    try:
        app_module.main()
    finally:
        app_module.GestureApp = original_app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
