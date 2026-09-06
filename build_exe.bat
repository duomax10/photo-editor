@echo off
REM Build a single-file PhotoTimestampEditor.exe into dist\.
setlocal
cd /d "%~dp0"

if not exist ".venv" (
    py -3 -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || goto :error
".venv\Scripts\python.exe" -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --name PhotoTimestampEditor ^
    run_app.py || goto :error

echo.
echo Built dist\PhotoTimestampEditor.exe
goto :eof

:error
echo Build failed.
pause
