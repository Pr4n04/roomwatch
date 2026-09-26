@echo off
REM Puts the power settings back to Windows defaults.
REM Needs an Administrator command prompt. Undoes keep_awake.bat.
setlocal

net session >nul 2>&1
if errorlevel 1 (
  echo  [X] Run this from an Administrator command prompt.
  pause
  exit /b 1
)

echo.
echo  Restoring Windows default power behaviour.
powercfg /change standby-timeout-ac 30
powercfg /hibernate on
powercfg /change lidclose-action-ac 3
echo.
echo  Done. Sleep after 30 min, hibernation on, lid close = sleep.
echo.
pause
