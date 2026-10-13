@echo off
rem Health check. Add the word "camera" to also test the webcam:  selfcheck.bat camera
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The virtual environment is missing. Run setup.bat first.
    pause
    exit /b 1
)
if /i "%~1"=="camera" (
    ".venv\Scripts\python.exe" main.py --selfcheck --camera
) else (
    ".venv\Scripts\python.exe" main.py --selfcheck
)
pause
