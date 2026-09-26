@echo off
REM Starts RoomWatch with no window at all, for autostart / Task Scheduler.
REM Everything is written to roomwatch.log instead of a console.
setlocal
cd /d "%~dp0"
start "" /b venv\Scripts\pythonw.exe roomwatch.py %*
