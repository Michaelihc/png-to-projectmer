@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "BOOTSTRAP_PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
if not defined BOOTSTRAP_PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)

if not defined BOOTSTRAP_PYTHON (
  echo Python 3.10 or newer is required.
  echo Download it from https://www.python.org/downloads/
  echo During setup, enable "Add Python to PATH", then run run-webapp.bat again.
  echo.
  pause
  exit /b 1
)

%BOOTSTRAP_PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required.
  echo Download it from https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating a private Python environment...
  %BOOTSTRAP_PYTHON% -m venv .venv
  if errorlevel 1 goto :setup_failed
)

set "APP_PYTHON=%CD%\.venv\Scripts\python.exe"
set "REQUIREMENTS_STAMP=.venv\requirements.installed.txt"

if not exist "%REQUIREMENTS_STAMP%" goto :install
fc /b "requirements.txt" "%REQUIREMENTS_STAMP%" >nul 2>nul
if errorlevel 1 goto :install
goto :launch

:install
echo Installing required packages. This only happens on the first run or after an update...
"%APP_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :setup_failed
copy /y "requirements.txt" "%REQUIREMENTS_STAMP%" >nul

:launch
echo.
echo Image to PMer is starting at http://127.0.0.1:8731/
echo Keep this window open while using the tool. Press Ctrl+C to stop it.
echo.
"%APP_PYTHON%" webapp\server.py %*
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
  echo.
  echo The tool stopped with an error.
  pause
)
exit /b %APP_EXIT%

:setup_failed
echo.
echo Setup failed. Check your internet connection, then run run-webapp.bat again.
echo If the problem continues, delete the .venv folder and retry.
echo.
pause
exit /b 1
