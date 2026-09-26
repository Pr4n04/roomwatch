@echo off
REM Start RoomWatch in a visible window so you can watch the log.
REM Ctrl+C stops it. Use this the first few times.
setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
  echo  Run setup.bat first.
  pause
  exit /b 1
)

echo  Starting RoomWatch. Close this window or press Ctrl+C to stop.
echo.
venv\Scripts\python.exe roomwatch.py %*
set "RC=%ERRORLEVEL%"
echo.
echo  RoomWatch exited with code %RC%.
if not "%RC%"=="0" echo  See roomwatch.log for the full log.
pause
