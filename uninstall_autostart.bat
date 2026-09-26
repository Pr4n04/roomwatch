@echo off
REM Removes the RoomWatch scheduled task created by install_autostart.bat.
setlocal
schtasks /end   /tn "RoomWatch" >nul 2>&1
schtasks /delete /tn "RoomWatch" /f
if errorlevel 1 (
  echo  No RoomWatch scheduled task was found - nothing to remove.
) else (
  echo  RoomWatch autostart removed.
)
echo.
echo  Your enrolled faces, settings and snapshots are untouched.
pause
