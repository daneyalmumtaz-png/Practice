@echo off
REM Puts a "Manual Assistant" icon on your Desktop that starts the app.
setlocal
set "TARGET=%~dp0Start Manual Assistant.bat"
set "WD=%~dp0.."
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Manual Assistant.lnk'); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=(Resolve-Path $env:WD).Path; $s.Save()"
echo.
echo Done. There is now a "Manual Assistant" icon on your Desktop.
pause
