@echo off
REM Launch the PNG -> ProjectMER schematic web UI.
setlocal
cd /d "%~dp0"

REM Prefer the Windows "py" launcher, fall back to python on PATH.
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

REM Ensure required packages are present; install them on first run.
%PY% -c "import cv2, numpy, skimage, mapbox_earcut" >nul 2>nul
if errorlevel 1 (
  echo First run: installing Python dependencies from requirements.txt ...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Dependency install failed. Try:  %PY% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
)

echo Starting converter... a browser tab will open shortly (default http://127.0.0.1:8731/).
%PY% webapp\server.py %*

pause
