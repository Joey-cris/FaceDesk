@echo off
setlocal
cd /d "%~dp0"
title Enable automatic startup
echo ============================================================
echo   Enable automatic startup (runs after YOU log in to Windows)
echo   It does NOT bypass or replace the Windows login screen.
echo ============================================================
echo.

set "PROJ=%~dp0"
if "%PROJ:~-1%"=="\" set "PROJ=%PROJ:~0,-1%"
set "EXE=%PROJ%\dist\FaceDesk\FaceDesk.exe"
set "PYW=%PROJ%\.venv\Scripts\pythonw.exe"
set "MAIN=%PROJ%\main.py"
set "OLD=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Master Joey Welcome Assistant.lnk"
if exist "%OLD%" del "%OLD%"
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FaceDesk Welcome.lnk"

set "MODE="
if exist "%EXE%" set "MODE=exe"
if not defined MODE if exist "%PYW%" set "MODE=py"
if not defined MODE (
    echo ERROR: neither the exe nor .venv was found. Run install_all.bat or setup.bat first.
    pause
    exit /b 1
)
echo Mode: %MODE%
if "%MODE%"=="exe" (
    if not exist "%PROJ%\dist\FaceDesk\data\model\lbph_model.yml" echo WARNING: no face registered in the exe yet. Open the exe and Register Face first.
) else (
    if not exist "%PROJ%\data\model\lbph_model.yml" echo WARNING: no registered face found. Register your face with run.bat first.
)
echo.
echo Have you already tested Register Face, Test Voice and Start Recognition
echo successfully?
choice /c YN /m "Enable automatic startup now"
if errorlevel 2 (
    echo Cancelled. Test the application first, then run this file again.
    pause
    exit /b 0
)

if "%MODE%"=="exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath=$env:EXE; $s.Arguments='--auto'; $s.WorkingDirectory=(Split-Path $env:EXE); $s.WindowStyle=7; $s.Description='FaceDesk'; $s.Save()"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath=$env:PYW; $s.Arguments=$q+$env:MAIN+$q+' --auto'; $s.WorkingDirectory=$env:PROJ; $s.WindowStyle=7; $s.Description='FaceDesk'; $s.Save()"
)
if errorlevel 1 (
    echo ERROR: could not create the startup shortcut.
    pause
    exit /b 1
)
if not exist "%LNK%" (
    echo ERROR: shortcut was not created.
    pause
    exit /b 1
)
echo.
echo Done. The assistant will start after your next Windows login.
echo To turn this off again, run disable_startup.bat
pause
