@echo off
REM ---------------------------------------------------------------- RoomWatch
REM Mutes alerts. The watcher keeps running and still detects people; it just
REM stops pushing to your phone. Takes effect within a second, no restart.
REM Double-click this file. To start alerts again, run resume.bat.
REM -----------------------------------------------------------------------
cd /d "%~dp0"
> "paused" echo RoomWatch is paused. Delete this file, or run resume.bat, to resume.
echo.
echo  Alerts are now MUTED. RoomWatch keeps watching, it just will not buzz.
echo  Run resume.bat to turn alerts back on.
echo.
pause
