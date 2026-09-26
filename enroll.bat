@echo off
REM ---------------------------------------------------------------- RoomWatch
REM Enrol a person so the watcher can put a name to their face.
REM Double-click this file, then type their name when asked.
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

set "WHO="
set /p WHO=Whose face are we adding? (e.g. Alice): 
if "%WHO%"=="" (
  echo.
  echo  Nothing typed, so there is nothing to do. Re-run and enter a name.
  echo.
  pause
  exit /b 1
)

echo.
echo  About to enrol "%WHO%".
echo.
echo  Close the Windows Camera app and any video call first, otherwise it will
echo  be holding the webcam and this will fail to start.
echo.
echo  You will need about 20 varied photos: different angles, different
echo  lighting, and so on. That is what decides whether names come out right.
echo.
pause

"venv\Scripts\python.exe" enroll.py "%WHO%"

echo.
pause
