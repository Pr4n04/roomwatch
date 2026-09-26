@echo off
REM ---------------------------------------------------------------- RoomWatch
REM Turns phone alerts back on after a pause. Double-click this file.
REM -----------------------------------------------------------------------
cd /d "%~dp0"
if exist "paused" del /q "paused"
echo.
echo  Alerts are back ON.
echo.
echo  Note: this only affects a RoomWatch that is already running. If it is not
echo  running, start it with start_roomwatch.bat.
echo.
pause
