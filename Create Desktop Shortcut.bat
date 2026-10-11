@echo off
REM Creates a desktop icon that launches Companion with one double-click.
set "TARGET=%~dp0Start Companion.bat"
set "ICON=%~dp0companion.ico"
set "WD=%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Companion.lnk'); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=$env:WD; $s.IconLocation=$env:ICON; $s.Save()"
echo.
echo Done. A "Companion" icon is now on your Desktop. Double-click it to run.
pause
