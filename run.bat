@echo off
REM Launch Photo Timestamp Editor from source on Windows.
setlocal
cd /d "%~dp0"

if not exist ".venv" (
    echo Creating a virtual environment...
    py -3 -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" -m photo_timestamp_editor
goto :eof

:error
echo.
echo Setup failed. Make sure Python 3.10 or newer is installed and on your PATH.
pause
