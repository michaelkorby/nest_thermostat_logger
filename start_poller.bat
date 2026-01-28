@echo off
REM Launch the Nest Thermostat poller scheduler for a 24-hour run.
REM Schedule this in Windows Task Scheduler to run once daily (e.g., at midnight).

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
REM Use venv's python directly to run the scheduler
"%VENV_DIR%\Scripts\python.exe" -m src.poller_scheduler %*
ENDLOCAL
