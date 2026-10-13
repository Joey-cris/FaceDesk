@echo off
setlocal
cd /d "%~dp0"
title Build executable
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
echo Installing PyInstaller ...
".venv\Scripts\python.exe" -m pip install pyinstaller==6.10.0
if errorlevel 1 goto :fail
echo Building FaceDesk ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name FaceDesk ^
    --collect-data cv2 ^
    --hidden-import pyttsx3.drivers --hidden-import pyttsx3.drivers.sapi5 ^
    --hidden-import comtypes --hidden-import pythoncom --hidden-import win32com ^
    main.py
if errorlevel 1 goto :fail
echo.
echo Build finished:  dist\FaceDesk\FaceDesk.exe
echo The exe keeps its data and config.json NEXT TO the exe. Register your face again
echo (or copy the "data" folder and config.json) and test it before relying on it.
if not defined JA_NOPAUSE pause
exit /b 0
:fail
echo.
echo BUILD FAILED - see the messages above.
if not defined JA_NOPAUSE pause
exit /b 1
