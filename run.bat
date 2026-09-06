@echo off
REM Run Photo Timestamp Editor from source. For everyday use prefer the
REM portable build produced by build_windows.bat, which needs no Python.
setlocal
cd /d "%~dp0"

call :find_python || goto :no_python

if not exist ".venv\Scripts\pythonw.exe" (
    echo First run: setting up. This takes a minute and only happens once.
    %PYTHON% -m venv .venv || goto :venv_failed
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
    echo Downloading Qt. Please wait...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :deps_failed
    echo Done.
)

REM pythonw launches the GUI with no console window left behind.
start "" ".venv\Scripts\pythonw.exe" -m photo_timestamp_editor
exit /b 0

:find_python
py -3 --version >nul 2>&1 && set "PYTHON=py -3" && exit /b 0
python --version >nul 2>&1 && set "PYTHON=python" && exit /b 0
exit /b 1

:no_python
echo.
echo Python was not found on this computer.
echo.
echo Either install Python 3.10 or newer from
echo     https://www.python.org/downloads/windows/
echo remembering to tick "Add python.exe to PATH" during setup,
echo.
echo or just use the portable build instead - it needs no Python at all.
echo.
pause
exit /b 1

:venv_failed
echo.
echo Could not create the virtual environment. If Python was installed from the
echo Microsoft Store, reinstall it from python.org instead.
echo.
pause
exit /b 1

:deps_failed
echo.
echo Could not install PySide6. Check your internet connection and try again.
echo If you are behind a proxy or company firewall, the portable build avoids
echo this problem entirely.
echo.
rmdir /s /q .venv 2>nul
pause
exit /b 1
