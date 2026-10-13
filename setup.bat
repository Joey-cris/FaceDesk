@echo off
setlocal
cd /d "%~dp0"
title FaceDesk - Setup
echo ============================================================
echo   FaceDesk - SETUP
echo ============================================================
echo.

echo [1/5] Looking for Python 3.12 ...
set "PYCMD="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYCMD=py -3.12"
if not defined PYCMD (
    python --version 2>&1 | findstr /B /C:"Python 3.12" >nul
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD goto :nopython
echo       Found: %PYCMD%
echo.

echo [2/5] Creating virtual environment in .venv ...
if exist ".venv\Scripts\python.exe" (
    echo       .venv already exists - reusing it.
) else (
    %PYCMD% -m venv .venv
    if errorlevel 1 goto :fail_venv
)
echo.

echo [3/5] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail_pip
echo.

echo [4/5] Installing dependencies - this can take a few minutes ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail_pip
echo.

echo [5/5] Verifying the installation ...
".venv\Scripts\python.exe" -c "import cv2, numpy, pyttsx3, tkinter; assert hasattr(cv2, 'face'), 'cv2.face missing'; print('      OpenCV', cv2.__version__, '- OK')"
if errorlevel 1 goto :fail_verify
echo.
echo ============================================================
echo   SETUP COMPLETE
echo   Next:  run.bat   then click "Register Face"
echo   Do NOT enable automatic startup until you have tested it.
echo ============================================================
if not defined JA_NOPAUSE pause
exit /b 0

:nopython
echo.
echo ERROR: Python 3.12 was not found.
echo   Fix 1: open a normal Command Prompt and run:
echo          winget install -e --id Python.Python.3.12
echo   Fix 2: download Python 3.12 from https://www.python.org/downloads/
echo          and tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" ticked.
echo   Then CLOSE this window and run setup.bat again.
if not defined JA_NOPAUSE pause
exit /b 1

:fail_venv
echo.
echo ERROR: Could not create the virtual environment.
echo   Try moving this folder to a short path such as C:\FaceDesk and run setup.bat again.
if not defined JA_NOPAUSE pause
exit /b 1

:fail_pip
echo.
echo ERROR: pip failed. Check your internet connection and try again.
echo   If pip is missing:   py -3.12 -m ensurepip --upgrade
echo   If a proxy or antivirus blocks pypi.org, allow it temporarily.
if not defined JA_NOPAUSE pause
exit /b 1

:fail_verify
echo.
echo ERROR: Installed packages could not be imported.
echo   If the message mentions cv2.face, run:
echo     .venv\Scripts\pip uninstall -y opencv-python opencv-contrib-python
echo     .venv\Scripts\pip install opencv-contrib-python==4.10.0.84
echo   If it mentions tkinter, reinstall Python 3.12 and keep "tcl/tk and IDLE" ticked.
if not defined JA_NOPAUSE pause
exit /b 1
