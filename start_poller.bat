@echo off
REM Long-running Nest Thermostat poller, intended to be called by the
REM NestPoller NSSM service. Runs indefinitely, polling every 5 minutes,
REM and exits cleanly on SIGTERM (which NSSM sends on service stop).

SETLOCAL
set SCRIPT_DIR=%~dp0
if "%SCRIPT_DIR:~-1%"=="\" set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%
cd /d "%SCRIPT_DIR%"

REM Check for venv in C:\venvs\ (standard location)
set VENV_DIR=C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
if exist "%VENV_DIR%\Scripts\python.exe" goto :run

REM Fall back to old locations in project directory (for migration period)
set VENV_DIR=%SCRIPT_DIR%\.venv_%COMPUTERNAME%
if exist "%VENV_DIR%\Scripts\python.exe" goto :run

set VENV_DIR=%SCRIPT_DIR%\.venv
if exist "%VENV_DIR%\Scripts\python.exe" goto :run

echo Virtual environment not found.
echo Expected one of:
echo   C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
echo   %SCRIPT_DIR%\.venv_%COMPUTERNAME%
echo   %SCRIPT_DIR%\.venv
echo.
echo To create a new venv in the standard location:
echo   py -3.12 -m venv C:\venvs\nest_thermostat_logger_%COMPUTERNAME%
exit /b 1

:run
if not exist "%SCRIPT_DIR%\logs" mkdir "%SCRIPT_DIR%\logs"
REM --duration 0 means run indefinitely; NSSM's stop signal triggers graceful shutdown
"%VENV_DIR%\Scripts\python.exe" -m src.poller_scheduler --duration 0
ENDLOCAL
