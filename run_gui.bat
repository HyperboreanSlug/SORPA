@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SOR Public Archiver

REM Resolve console Python (for pip + path discovery)
set "USE_PY_LAUNCHER=0"
set "PY="
where py >nul 2>&1 && (
  set "USE_PY_LAUNCHER=1"
  set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
)
if not defined PY (
  echo ERROR: No Python found.>"%~dp0gui_error.log"
  echo ERROR: No Python found.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo Check "Add python.exe to PATH".
  pause
  exit /b 1
)

REM Absolute path of this interpreter → pair with pythonw.exe (no console)
set "PYEXE="
if "%USE_PY_LAUNCHER%"=="1" (
  for /f "delims=" %%I in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
) else (
  for /f "delims=" %%I in ('"%PY%" -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%I"
)
if not defined PYEXE (
  echo ERROR: Could not resolve Python path.>"%~dp0gui_error.log"
  echo ERROR: Could not resolve Python path.
  pause
  exit /b 1
)

set "PYWEXE=%PYEXE%"
if /i "%PYEXE:~-10%"=="python.exe" (
  set "PYWEXE=%PYEXE:~0,-10%pythonw.exe"
)
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"

REM Quiet dependency install (gui.py also bootstraps if needed)
"%PYEXE%" -m pip install --user -q -r "%~dp0requirements.txt" 2>nul
if errorlevel 1 (
  "%PYEXE%" -m pip install -q -r "%~dp0requirements.txt" 2>nul
)
if errorlevel 1 (
  echo pip install failed.>"%~dp0gui_error.log"
  echo pip install failed — try: "%PYEXE%" -m pip install -r requirements.txt
  pause
  exit /b 1
)

REM Detach GUI so this console can exit immediately (no lingering CMD)
start "" "%PYWEXE%" "%~dp0gui.py"
endlocal
exit /b 0
