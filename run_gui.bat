@echo off
REM Double-click launcher for the SOR Public Archiver GUI.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found on PATH.
  echo Install Python 3.10+ and check "Add python.exe to PATH".
  pause
  exit /b 1
)

python -c "import customtkinter" >nul 2>&1
if errorlevel 1 (
  echo Installing dependencies...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
  )
)

python "%~dp0gui.py"
if errorlevel 1 (
  echo.
  echo GUI exited with an error. See gui_error.log if present.
  pause
)
