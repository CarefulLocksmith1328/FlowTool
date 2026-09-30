@echo off
setlocal
set "SOURCE=%~dp0FlowTool"
set "TARGET=%LOCALAPPDATA%\Programs\FlowTool"
if not exist "%SOURCE%\FlowTool.exe" (
  echo FlowTool.exe fehlt. Bitte das gesamte ZIP entpacken und erneut starten.
  pause
  exit /b 1
)
tasklist /FI "IMAGENAME eq FlowTool.exe" /NH | findstr /I /B "FlowTool.exe" >nul
if not errorlevel 1 (
  echo Bitte FlowTool vor der Installation beenden und erneut starten.
  pause
  exit /b 1
)
echo FlowTool wird fuer diesen Windows-Benutzer installiert ...
robocopy "%SOURCE%" "%TARGET%" /E /R:1 /W:1 >nul
if errorlevel 8 (
  echo Kopieren fehlgeschlagen.
  pause
  exit /b 1
)
powershell -NoProfile -Command "$target=Join-Path $env:LOCALAPPDATA 'Programs\FlowTool'; $shell=New-Object -ComObject WScript.Shell; foreach($path in @((Join-Path ([Environment]::GetFolderPath('Programs')) 'FlowTool.lnk'),(Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'FlowTool.lnk'))) { $link=$shell.CreateShortcut($path); $link.TargetPath=Join-Path $target 'FlowTool.exe'; $link.WorkingDirectory=$target; $link.Description='Lokaler Flow-Editor'; $link.Save() }"
if errorlevel 1 (
  echo Verknuepfungen konnten nicht angelegt werden. FlowTool.exe wurde nach "%TARGET%" kopiert.
  pause
  exit /b 1
)
echo FlowTool ist installiert. Die Anwendung startet jetzt.
start "" "%TARGET%\FlowTool.exe"
exit /b 0

