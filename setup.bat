@echo off
REM ---------------------------------------------------------------- RoomWatch
REM One-time setup: creates a virtual environment, installs dependencies and
REM downloads the two face models. Run this once, double-click is fine.
REM ASCII only on purpose, so it works on any Windows code page.
REM -----------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo  RoomWatch setup
echo  ==============
echo.

REM --- locate a usable Python -----------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo  [X] Python was not found on this machine.
  echo.
  echo      Install Python 3.11 or 3.12 from https://www.python.org/downloads/
  echo      and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

REM Resolve the real executable path first: inside an if-block a %VAR% set in
REM that same block expands to nothing, and we need this to diagnose the Store
REM placeholder below.
set "PYEXE="
for /f "delims=" %%P in ('%PY% -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"

echo  [1/4] Using Python launcher: %PY%
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>&1
if errorlevel 1 (
  echo  [X] "%PY%" is on PATH but is not a working Python 3.9 or newer.
  if defined PYEXE echo      It points at: %PYEXE%
  echo.
  if not "%PYEXE:WindowsApps=%"=="%PYEXE%" (
    echo      That is the Microsoft Store placeholder. It is named python.exe
    echo      but cannot actually run anything, so the RoomWatch scripts would
    echo      fail in a very confusing way. Uninstall it and install the real
    echo      Python instead:
    echo.
    echo        1. Open https://www.python.org/downloads/
    echo        2. Download Python 3.12 for Windows ^(64-bit^)
    echo        3. Tick "Add python.exe to PATH" on the FIRST screen
    echo        4. Do NOT install Python from the Microsoft Store
  ) else (
    echo      Install Python 3.11 or 3.12 from https://www.python.org/downloads/
    echo      and tick "Add python.exe to PATH" on the first screen.
  )
  echo.
  pause
  exit /b 1
)
%PY% -c "import sys; print('      Python', sys.version.split()[0])"

REM --- virtual environment ---------------------------------------------
echo  [2/4] Creating virtual environment in .\venv
if exist "venv\Scripts\python.exe" (
  echo      Already exists, reusing it.
) else (
  %PY% -m venv venv
  if errorlevel 1 (
    echo  [X] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

set "VPY=%CD%\venv\Scripts\python.exe"

REM --- dependencies ----------------------------------------------------
echo  [3/4] Installing packages. This can take a minute...
"%VPY%" -m pip install --upgrade pip --quiet
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  [X] Package install failed. The usual cause on Windows is a corporate
  echo      proxy or antivirus blocking pypi.org. Try:
  echo          "%VPY%" -m pip install -r requirements.txt --index-url https://pypi.org/simple
  echo.
  pause
  exit /b 1
)

REM --- face models ------------------------------------------------------
echo  [4/4] Downloading the face detection and recognition models (~40 MB)
"%VPY%" fetch_models.py
if errorlevel 1 (
  echo.
  echo  [X] Model download failed. If your network blocks GitHub, download both
  echo      files in a browser and drop them into .\models :
  echo        face_detection_yunet_2023mar.onnx
  echo        https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
  echo        face_recognition_sface_2021dec.onnx
  echo        https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
  echo.
  pause
  exit /b 1
)

REM --- settings file ------------------------------------------------------
REM config.json is deliberately not tracked in git: your ntfy topic is a
REM secret on a public server. So seed it from the committed template.
if not exist "config.json" (
  if exist "config.example.json" (
    copy /y "config.example.json" "config.json" >nul
    echo  Creating config.json from config.example.json
  )
)

echo.
echo  ==============================
echo   Setup complete.
echo  ==============================
echo.
echo  Next, three steps:
echo.
echo    1. Tell it who lives with you. Close the Windows Camera app first,
echo       then for each person run:
echo.
echo           venv\Scripts\python.exe enroll.py "Name"
echo.
echo       Take 20 varied photos each. This is the single thing that decides
echo       whether names come out right.
echo.
echo    2. Turn on phone notifications. Run this first to check the link:
echo.
echo           venv\Scripts\python.exe test_phone.py
echo
echo       It will walk you through ntfy, which needs no account or token.
echo       See README.md section 3.
echo.
echo    3. Check everything is wired up, then start it:
echo.
echo           venv\Scripts\python.exe roomwatch.py --check
echo           venv\Scripts\python.exe roomwatch.py
echo.
pause
