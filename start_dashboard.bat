@echo off
REM Launch the Nest Thermostat Streamlit dashboard from the project root.

SETLOCAL
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Check for venv in C:\venvs\ (new location)
set VENV_DIR=C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
if exist "%VENV_DIR%\Scripts\activate.bat" goto :activate

REM Fall back to old locations in project directory (for migration period)
set VENV_DIR=%SCRIPT_DIR%.venv_%COMPUTERNAME%
if exist "%VENV_DIR%\Scripts\activate.bat" goto :activate

set VENV_DIR=%SCRIPT_DIR%.venv
if exist "%VENV_DIR%\Scripts\activate.bat" goto :activate

echo Virtual environment not found.
echo Expected one of:
echo   C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
echo   %SCRIPT_DIR%\.venv_%COMPUTERNAME%
echo   %SCRIPT_DIR%\.venv
echo.
echo To create a new venv in the standard location:
echo   py -3.12 -m venv C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
exit /b 1

:activate
call "%VENV_DIR%\Scripts\activate.bat"
streamlit run src\dashboard.py --browser.gatherUsageStats=false --server.headless=true
ENDLOCAL

