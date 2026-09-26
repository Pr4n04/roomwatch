@echo off
REM ---------------------------------------------------------------- RoomWatch
REM Checks that RoomWatch can actually open the camera and read frames from it.
REM Run this before you start pointing the laptop at the room. Double-click me.
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

echo  Checking the camera...
echo.
"venv\Scripts\python.exe" roomwatch.py --check
echo.
echo  If it says OK, the watcher can see the camera. If it says FAILED, see
echo  the Troubleshooting section of README.md -- the most common causes are the
echo  keyboard camera key and the Windows camera privacy setting.
echo.
pause
