@echo off
rem Opens FaceDesk and starts ATTENDANCE mode immediately (kiosk style).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment is missing. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py --attendance
if errorlevel 1 (
    echo.
    echo FaceDesk exited with an error. See logs\assistant.log and README.md Troubleshooting.
    pause
)
