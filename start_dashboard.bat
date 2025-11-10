@echo off
REM Launch the Nest Thermostat Streamlit dashboard from the project root.

SETLOCAL
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

set VENV_DIR=.venv_%COMPUTERNAME%
if exist "%VENV_DIR%\Scripts\activate.bat" goto :activate

set VENV_DIR=.venv
if exist "%VENV_DIR%\Scripts\activate.bat" goto :activate

echo Virtual environment not found.
echo Expected one of:
echo   %SCRIPT_DIR%\.venv_%COMPUTERNAME%
echo   %SCRIPT_DIR%\.venv
echo Please create it with: py -3.12 -m venv %VENV_DIR%
exit /b 1

:activate
call "%VENV_DIR%\Scripts\activate.bat"
streamlit run src\dashboard.py
ENDLOCAL

