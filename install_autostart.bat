@echo off
REM Adds a scheduled task so RoomWatch starts every time you log in to Windows.
REM Run it from an ordinary (non-admin) command prompt. Uninstall with
REM uninstall_autostart.bat.
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\pythonw.exe" (
  echo  Run setup.bat first - there is no venv\Scripts\pythonw.exe.
  pause
  exit /b 1
)

if not exist "config.json" (
  echo  config.json is missing. Unzip RoomWatch again and retry.
  pause
  exit /b 1
)

REM Absolute path with a delay, so Explorer/network drives are mounted and the
REM previous session has fully released the webcam before we claim it.
set "TASK=RoomWatch"
set "CMD=\"%CD%\venv\Scripts\pythonw.exe\" \"%CD%\roomwatch.py\" --startup-delay 8"

schtasks /delete /tn "%TASK%" /f >nul 2>&1
schtasks /create /tn "%TASK%" /tr "%CMD%" /sc onlogon /delay 0000:20 /rl limited /f
if errorlevel 1 (
  echo.
  echo  [X] Could not create the scheduled task. Try right-click this file and
  echo      "Run as administrator", or fall back to the Startup folder:
  echo.
  echo      shell:startup
  echo      and drop a shortcut to start_hidden.bat in there.
  echo.
  pause
  exit /b 1
)

echo.
echo  Scheduled task "%TASK%" created - RoomWatch now starts at every logon.
echo.
echo    Stop it now:   schtasks /end /tn "%TASK%"
echo    Start it now:  schtasks /run  /tn "%TASK%"
echo    Check it:      schtasks /query /tn "%TASK%"
echo    Live log:      notepad roomwatch.log
echo.
echo  IMPORTANT: the laptop must stay awake, lid open and logged in. See the
echo  "Keeping it running" section of README.md.
echo.
pause
