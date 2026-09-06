@echo off
rem mc-print-3d launcher (Windows). Creates the virtual environment on first run.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv || python -m venv .venv || goto :nopython
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if "%~1"=="" (
    ".venv\Scripts\python.exe" -m mcprint gui
) else (
    ".venv\Scripts\python.exe" -m mcprint %*
)
goto :eof
:nopython
echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and run this file again.
pause
