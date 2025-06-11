@echo off
REM Mendys Robot Scraper Platform - CI/CD Manager (Windows)
REM Wrapper script for ci_cd_manager.py

setlocal EnableDelayedExpansion

REM Get script directory
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

REM Change to project root
cd /d "%PROJECT_ROOT%"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.9+ and ensure it's accessible from command line
    pause
    exit /b 1
)

REM Check if arguments provided
if "%1"=="" (
    echo.
    echo Mendys Robot Scraper Platform - CI/CD Manager
    echo.
    echo Usage: %~nx0 ^<command^>
    echo.
    echo Commands:
    echo   check-all       Run all checks (lint, test, security)
    echo   check-python    Run Python linting and testing
    echo   check-frontend  Run frontend linting and testing
    echo.
    echo Examples:
    echo   %~nx0 check-all
    echo   %~nx0 check-python
    echo   %~nx0 check-frontend
    echo.
    pause
    exit /b 1
)

REM Run the Python script with all arguments
echo Running CI/CD Manager: %*
echo.
python scripts-tools\ci_cd_manager.py %*

REM Capture exit code
set EXIT_CODE=%ERRORLEVEL%

REM Show result
echo.
if %EXIT_CODE%==0 (
    echo ✅ CI/CD checks completed successfully!
) else (
    echo ❌ CI/CD checks failed with exit code %EXIT_CODE%
)

REM Pause if run directly (not from another script)
echo %CMDCMDLINE% | find /i "/c" >nul && pause

exit /b %EXIT_CODE% 