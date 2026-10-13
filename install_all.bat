@echo off
setlocal
cd /d "%~dp0"
title FaceDesk - Install All
echo ============================================================
echo   ONE-CLICK INSTALL: setup + build exe + Desktop shortcut
echo ============================================================
set "JA_NOPAUSE=1"

call setup.bat
if errorlevel 1 goto :fail

call build_exe.bat
if errorlevel 1 goto :noexe

set "EXE=%~dp0dist\FaceDesk\FaceDesk.exe"
if not exist "%EXE%" goto :noexe
set "TARGET=%EXE%"
set "WORKDIR=%~dp0dist\FaceDesk"
goto :shortcut

:noexe
echo.
echo NOTE: the exe could not be built. A Desktop shortcut to run.bat will be made instead.
set "TARGET=%~dp0run.bat"
set "WORKDIR=%~dp0"

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\FaceDesk.lnk'); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=$env:WORKDIR; $s.Description='FaceDesk'; $s.Save()"
echo.
echo ============================================================
echo   DONE. Use the "FaceDesk" shortcut on your Desktop.
echo   1. Open it, click Register Face, Test Voice, Start Recognition.
echo   2. When it works, run enable_startup.bat.
echo ============================================================
set "JA_NOPAUSE="
pause
exit /b 0

:fail
echo.
echo INSTALL STOPPED - see the error above.
set "JA_NOPAUSE="
pause
exit /b 1
