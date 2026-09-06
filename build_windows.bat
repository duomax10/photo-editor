@echo off
REM Build the portable Windows folder + ZIP into dist\.
REM Requires Python 3.10+ on this machine; the people you hand the ZIP to do not need it.
REM
REM If you have no Windows machine, use GitHub Actions instead:
REM   Actions tab -> "Build Windows portable" -> Run workflow.
setlocal
cd /d "%~dp0"

call :find_python || goto :no_python

echo Preparing the build environment...
if not exist ".venv-build" (
    %PYTHON% -m venv .venv-build || goto :failed
)
set BUILD_PY=.venv-build\Scripts\python.exe
"%BUILD_PY%" -m pip install --upgrade pip --quiet || goto :failed
"%BUILD_PY%" -m pip install -r requirements.txt pyinstaller --quiet || goto :failed

echo Building...
rmdir /s /q dist\PhotoTimestampEditor 2>nul
"%BUILD_PY%" -m PyInstaller packaging\PhotoTimestampEditor.spec --noconfirm --clean || goto :failed

echo Checking the built app starts...
"%BUILD_PY%" -c "import subprocess,sys; sys.exit(subprocess.run(['dist\\PhotoTimestampEditor\\PhotoTimestampEditor.exe','--selftest']).returncode)" || goto :selftest_failed

REM Shared with the GitHub Actions workflow so the ZIP is named in one place.
"%BUILD_PY%" tools\package_portable.py || goto :failed

echo.
echo   Folder : dist\PhotoTimestampEditor\  (run PhotoTimestampEditor.exe)
echo   ZIP    : see the path printed above
echo.
echo Hand out the ZIP. Users extract it and double-click PhotoTimestampEditor.exe.
pause
goto :eof

:find_python
py -3 --version >nul 2>&1 && set "PYTHON=py -3" && exit /b 0
python --version >nul 2>&1 && set "PYTHON=python" && exit /b 0
exit /b 1

:no_python
echo.
echo Python 3.10 or newer is required to BUILD the app (not to run it).
echo Download it from https://www.python.org/downloads/windows/
echo and tick "Add python.exe to PATH" during setup.
echo.
echo Alternatively build it on GitHub: Actions tab -^> "Build Windows portable".
echo.
pause
exit /b 1

:selftest_failed
echo.
echo The app was built but would not start. Something the PyInstaller spec
echo excludes in packaging\PhotoTimestampEditor.spec is actually needed.
echo.
pause
exit /b 1

:failed
echo.
echo Build failed. Scroll up for the first error.
pause
exit /b 1
