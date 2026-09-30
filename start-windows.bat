@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 app.py
  goto done
)
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  python app.py
  goto done
)
echo Python 3.10 oder neuer wurde nicht gefunden.
echo Installiere Python und starte diese Datei erneut.
:done
pause
