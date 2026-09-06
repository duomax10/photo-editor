@echo off
REM Build the portable Windows folder + ZIP into dist\.
REM Requires Python 3.10+ on this machine; the people you hand the ZIP to do not need it.
setlocal enabledelayedexpansion
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

REM The marker that tells the app to keep its settings and undo logs
REM inside the folder rather than in AppData.
echo This file makes the app portable: settings and undo logs are kept in the> "dist\PhotoTimestampEditor\portable.txt"
echo "data" folder next to the program instead of in your Windows user profile.>> "dist\PhotoTimestampEditor\portable.txt"
echo Delete this file if you would rather it used AppData.>> "dist\PhotoTimestampEditor\portable.txt"

copy /y packaging\READ-ME-FIRST.txt "dist\PhotoTimestampEditor\READ-ME-FIRST.txt" >nul

for /f %%v in ('"%BUILD_PY%" -c "import photo_timestamp_editor as p; print(p.__version__)"') do set VERSION=%%v
set ZIP=dist\PhotoTimestampEditor-%VERSION%-windows-x64.zip
del "%ZIP%" 2>nul
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PhotoTimestampEditor' -DestinationPath '%ZIP%' -CompressionLevel Optimal" || goto :failed

echo.
echo   Folder : dist\PhotoTimestampEditor\  (run PhotoTimestampEditor.exe)
echo   ZIP    : %ZIP%
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
pause
exit /b 1

:failed
echo.
echo Build failed. Scroll up for the first error.
pause
exit /b 1
