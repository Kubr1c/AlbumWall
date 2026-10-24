@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (echo Please install Python 3.12 and retry.& pause & exit /b 2)
  ".venv\Scripts\python.exe" -m pip install ytmusicapi==1.12.3 Pillow
  if errorlevel 1 (pause & exit /b 2)
)
echo AlbumWall v3 - Manual update. Close albums.csv in Excel before continuing.
".venv\Scripts\python.exe" update.py --youtube %*
set "result=%errorlevel%"
echo.
echo Report: .local\last_report.json
pause
exit /b %result%
