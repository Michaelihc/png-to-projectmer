@echo off
REM Quick-serve the converter page (assumes dependencies are already installed).
REM First time? Use run-webapp.bat instead — it installs deps for you.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py") || (set "PY=python")
echo Serving at http://127.0.0.1:8731/  (Ctrl+C to stop)
%PY% webapp\server.py %*
pause
