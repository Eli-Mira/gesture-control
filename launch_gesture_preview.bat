@echo off
setlocal
rem Portable gesture launcher: root is this script's directory, not the caller's CWD.
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "MODEL=%ROOT%hand_landmarker.task"
if not exist "%PYTHON%" (
    echo Gesture preview blocked: project .venv Python not found.
    exit /b 2
)
if not exist "%MODEL%" (
    echo Gesture preview blocked: hand_landmarker.task missing.
    exit /b 2
)
pushd "%ROOT%" || exit /b 2
"%PYTHON%" -m gesture_control.release --check-only
if errorlevel 1 (
    echo Gesture preview blocked: model integrity check failed.
    popd
    exit /b 2
)
rem App startup is disarmed/preview by default; live input requires explicit UI arming.
"%PYTHON%" -m gesture_control.release --preview-only
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
