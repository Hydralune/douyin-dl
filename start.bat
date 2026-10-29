@echo off
setlocal
cd /d "%~dp0"

if not exist "gui.py" (
    echo [ERROR] gui.py not found in %CD%
    echo Please put this .bat next to gui.py
    pause
    exit /b 1
)

rem Prefer pythonw (no console window) when available
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw "gui.py"
    exit /b 0
)

where python >nul 2>&1
if %errorlevel%==0 (
    start "" python "gui.py"
    exit /b 0
)

echo [ERROR] Python not found in PATH
echo Install Python 3.10+ and enable "Add Python to PATH"
pause
exit /b 1
