@echo off
REM Run this once. It installs what the app needs and asks for a sign-in account.
setlocal
cd /d "%~dp0.."

echo Manual Assistant: setting up
echo ===========================
echo.

where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python is not installed, or not on the PATH.
    echo.
    echo 1. Go to https://www.python.org/downloads/
    echo 2. Download Python 3.12 or newer.
    echo 3. IMPORTANT: on the first screen of the installer, tick
    echo    "Add python.exe to PATH" before pressing Install.
    echo 4. Then run this file again.
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
) else (
  set "PY=py -3"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python folder for the app. This takes a moment.
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the folder. See the message above.
    pause
    exit /b 1
  )
)

echo Installing the app's components. The first time this takes a few minutes.
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
  echo Installing the components failed. See the message above.
  pause
  exit /b 1
)
echo Components installed.
echo.

".venv\Scripts\python.exe" "scripts\first_run.py"
set RC=%errorlevel%
echo.
if %RC% neq 0 (
  echo Setup did not finish. Read the message above, fix it, and run this again.
) else (
  echo Setup finished. Now double-click "Start Manual Assistant.bat".
)
pause
exit /b %RC%
