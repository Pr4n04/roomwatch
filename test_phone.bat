@echo off
REM ---------------------------------------------------------------- RoomWatch
REM Sends one test photo + one line of text to your phone.
REM No camera and no enrolled faces needed, so it is the quickest way to prove
REM the phone link works. Double-click this file.
REM ASCII only on purpose, so it works on any Windows code page.
REM -----------------------------------------------------------------------
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo.
  echo  [X] No virtual environment found here.
  echo.
  echo      Run setup.bat first and let it finish.
  echo.
  pause
  exit /b 1
)

echo  Sending a test alert to your phone. Check it in a few seconds.
echo.
"venv\Scripts\python.exe" test_phone.py
echo.
pause
