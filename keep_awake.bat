@echo off
REM Stops Windows from putting the laptop to sleep while RoomWatch watches.
REM Needs an Administrator command prompt: right-click cmd, "Run as
REM administrator", then run this file.
REM
REM WARNING, READ FIRST
REM   This switches the lid-close action to "do nothing" and disables sleep and
REM   hibernation on mains power. That is what you want for a laptop sitting open
REM   on a stand all day. It is NOT what you want before putting it in a bag.
REM   undo_keep_awake.bat puts every one of these back the way it was.
setlocal

net session >nul 2>&1
if errorlevel 1 (
  echo  [X] This needs an Administrator command prompt.
  echo      Right-click Command Prompt, choose "Run as administrator", then run:
  echo      %~f0
  echo.
  pause
  exit /b 1
)

echo.
echo  Changing power settings. Current values first, for the record:
echo.
powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
echo.

echo  [1/3] Sleep on mains power: never
powercfg /change standby-timeout-ac 0

echo  [2/3] Hibernate on mains power: off
powercfg /hibernate off

echo  [3/3] Lid close on mains power: do nothing
powercfg /change lidclose-action-ac 2

echo.
echo  Done. RoomWatch can now keep running with the screen off and the lid open.
echo  The screen itself still turns off on the normal timer, which is fine.
echo.
echo  To put all of this back:  undo_keep_awake.bat
echo.
pause
