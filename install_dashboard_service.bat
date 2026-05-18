@echo off
REM Install the NestDashboard Windows service via NSSM.
REM Must be run from an elevated (Administrator) prompt.

SETLOCAL

REM ── Check for admin privileges ────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This script must be run as Administrator.
    echo Right-click the file and choose "Run as administrator".
    exit /b 1
)

REM ── Resolve project root (folder containing this script) ─────────────────
set PROJECT_DIR=%~dp0
REM Strip trailing backslash
if "%PROJECT_DIR:~-1%"=="\" set PROJECT_DIR=%PROJECT_DIR:~0,-1%

REM Common manual install location
set NSSM= "C:\\nssm\win64\nssm.exe" 
echo Using NSSM: %NSSM%
echo Project directory: %PROJECT_DIR%
echo.

REM ── Remove any existing service first (idempotent re-installs) ────────────
%NSSM% status NestDashboard >nul 2>&1
if %errorlevel% equ 0 (
    echo Removing existing NestDashboard service...
    %NSSM% stop NestDashboard confirm >nul 2>&1
    %NSSM% remove NestDashboard confirm
    if %errorlevel% neq 0 (
        echo ERROR: Failed to remove existing service.
        exit /b 1
    )
)

REM ── Create the service ────────────────────────────────────────────────────
echo Installing NestDashboard service...
%NSSM% install NestDashboard "%PROJECT_DIR%\start_dashboard.bat"
if %errorlevel% neq 0 (
    echo ERROR: nssm install failed.
    exit /b 1
)

REM ── Configure the service ─────────────────────────────────────────────────
%NSSM% set NestDashboard AppDirectory "%PROJECT_DIR%"
%NSSM% set NestDashboard AppStdout "%PROJECT_DIR%\logs\dashboard.log"
%NSSM% set NestDashboard AppStderr "%PROJECT_DIR%\logs\dashboard_error.log"
%NSSM% set NestDashboard AppRotateFiles 1
%NSSM% set NestDashboard AppRotateBytes 1048576
%NSSM% set NestDashboard Start SERVICE_AUTO_START

REM ── Ensure logs directory exists ──────────────────────────────────────────
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

REM ── Start the service ─────────────────────────────────────────────────────
echo Starting NestDashboard service...
%NSSM% start NestDashboard
if %errorlevel% neq 0 (
    echo ERROR: Service installed but failed to start.
    echo Check logs\dashboard_error.log or run start_dashboard.bat manually to diagnose.
    exit /b 1
)

echo.
echo NestDashboard service installed and started successfully.
echo Dashboard should be available at http://localhost:8501
echo.
echo Useful commands:
echo   nssm status NestDashboard
echo   nssm stop NestDashboard
echo   nssm restart NestDashboard
echo   nssm edit NestDashboard

ENDLOCAL
