@echo off
REM Double-click to start the app. Leave this window open while using it.
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo The app has not been set up yet.
  echo Double-click "Setup Manual Assistant.bat" first.
  echo.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "scripts\start.py"
echo.
pause
