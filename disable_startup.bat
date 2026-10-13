@echo off
setlocal
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FaceDesk Welcome.lnk"
set "OLD=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Master Joey Welcome Assistant.lnk"
if exist "%OLD%" del "%OLD%"
if exist "%LNK%" (
    del "%LNK%"
    echo Automatic startup disabled.
) else (
    echo Automatic startup was not enabled.
)
pause
