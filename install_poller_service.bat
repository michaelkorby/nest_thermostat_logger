@echo off
REM Install the NestPoller Windows service via NSSM.
REM Must be run from an elevated (Administrator) prompt.
REM
REM The service runs start_poller.bat, which calls poller_scheduler.py with
REM --duration 0 (run forever). NSSM sends SIGTERM on stop, which the scheduler
REM handles gracefully, finishing the current poll before exiting.
REM
REM IMPORTANT - SERVICE ACCOUNT:
REM   Run the service as your Windows user account (not Local System) so it can
REM   access project files stored in your Google Drive folder. You will be prompted
REM   for your username and password below. The account is granted "Log on as a
REM   service" automatically by NSSM.

SETLOCAL ENABLEDELAYEDEXPANSION

REM ── Check for admin privileges ────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This script must be run as Administrator.
    echo Right-click the file and choose "Run as administrator".
    exit /b 1
)

REM ── Resolve project root (folder containing this script) ─────────────────
set PROJECT_DIR=%~dp0
if "%PROJECT_DIR:~-1%"=="\" set PROJECT_DIR=%PROJECT_DIR:~0,-1%

REM ── Locate nssm.exe ───────────────────────────────────────────────────────
set NSSM="C:\nssm\win64\nssm.exe"
echo Using NSSM: %NSSM%
echo Project directory: %PROJECT_DIR%
echo.

REM ── Prompt for service account credentials ────────────────────────────────
echo This service needs to run as your Windows user account to access Google Drive
echo files. Enter your credentials below (leave blank to use Local System, which
echo will NOT have access to files stored in your Google Drive folder).
echo.
echo   For a local account use the format:  .\username   (e.g. .\mkorb)
echo   For a domain account use the format: DOMAIN\username
echo.
set /p SVC_USER="Service account (blank = Local System): "

REM Auto-prepend .\ if the user typed a bare name with no domain separator.
REM Detect a backslash by substituting it out and comparing; if the strings
REM are equal there was no backslash, so the user typed a bare username.
if not "!SVC_USER!"=="" (
    set _NOBACKSLASH=!SVC_USER:\=!
    if "!_NOBACKSLASH!"=="!SVC_USER!" set SVC_USER=.\!SVC_USER!
    set _NOBACKSLASH=
)

if "!SVC_USER!"=="" (
    set SVC_PASS=
    echo Running as Local System.
) else (
    set /p SVC_PASS="Password for !SVC_USER!: "
)
echo.

REM ── Remove any existing service first (idempotent re-installs) ────────────
%NSSM% status NestPoller >nul 2>&1
if !errorlevel! equ 0 (
    echo Removing existing NestPoller service...
    %NSSM% stop NestPoller confirm >nul 2>&1
    %NSSM% remove NestPoller confirm
    if !errorlevel! neq 0 (
        echo ERROR: Failed to remove existing service.
        exit /b 1
    )
)

REM ── Create the service ────────────────────────────────────────────────────
echo Installing NestPoller service...
%NSSM% install NestPoller "%PROJECT_DIR%\start_poller.bat"
if %errorlevel% neq 0 (
    echo ERROR: nssm install failed.
    exit /b 1
)

REM ── Configure the service ─────────────────────────────────────────────────
%NSSM% set NestPoller AppDirectory "%PROJECT_DIR%"
%NSSM% set NestPoller AppStdout "%PROJECT_DIR%\logs\poller_service.log"
%NSSM% set NestPoller AppStderr "%PROJECT_DIR%\logs\poller_service_error.log"
%NSSM% set NestPoller AppRotateFiles 1
%NSSM% set NestPoller AppRotateBytes 5242880
%NSSM% set NestPoller Start SERVICE_AUTO_START

REM Stop method: send Ctrl+C first (so Python catches SIGINT/SIGTERM), then kill
%NSSM% set NestPoller AppStopMethodConsole 5000
%NSSM% set NestPoller AppStopMethodWindow 1000
%NSSM% set NestPoller AppStopMethodThreads 1000

REM ── Set service account (if provided) ────────────────────────────────────
if not "!SVC_USER!"=="" (
    echo Configuring service to run as !SVC_USER!...
    %NSSM% set NestPoller ObjectName "!SVC_USER!" "!SVC_PASS!"
    if !errorlevel! neq 0 (
        echo ERROR: Failed to set service account. Check the username and password.
        echo Tip: Use .\username for a local account, e.g. .\mkorb
        %NSSM% remove NestPoller confirm >nul 2>&1
        exit /b 1
    )
)

REM ── Ensure logs directory exists ──────────────────────────────────────────
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

REM ── Remove the old Task Scheduler task if it exists ───────────────────────
schtasks /query /tn "Nest Thermostat Logger" >nul 2>&1
if !errorlevel! equ 0 (
    echo Removing old Task Scheduler task "Nest Thermostat Logger"...
    schtasks /delete /tn "Nest Thermostat Logger" /f
    if !errorlevel! equ 0 (
        echo Task Scheduler task removed.
    ) else (
        echo WARNING: Could not remove Task Scheduler task. Remove it manually to avoid duplicate polling.
    )
)

REM ── Start the service ─────────────────────────────────────────────────────
echo Starting NestPoller service...
%NSSM% start NestPoller
if %errorlevel% neq 0 (
    echo ERROR: Service installed but failed to start.
    echo Check logs\poller_service_error.log or run start_poller.bat manually to diagnose.
    exit /b 1
)

echo.
echo NestPoller service installed and started successfully.
echo The poller runs every 5 minutes and will restart automatically with Windows.
echo No user login is required for the service to run.
echo.
echo Log files:
echo   %PROJECT_DIR%\logs\poller_service.log
echo   %PROJECT_DIR%\logs\poller_service_error.log
echo.
echo Useful commands:
echo   nssm status NestPoller
echo   nssm stop NestPoller
echo   nssm restart NestPoller
echo   nssm edit NestPoller

ENDLOCAL
