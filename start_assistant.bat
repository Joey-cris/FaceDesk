@echo off
rem Silent launcher: starts the assistant in startup mode with NO console window.
rem Works from any current directory because every path is resolved from this file's location.
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" exit /b 1
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py" --auto
exit /b 0
