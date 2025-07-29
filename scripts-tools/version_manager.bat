@echo off
REM Version Manager - Windows Batch Wrapper for FuzeInfra Platform
REM This script provides a convenient way to run version_manager.py on Windows

setlocal

REM Change to the project root directory
cd /d "%~dp0\.."

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Error: Python is not installed or not in PATH
    echo Please install Python 3.7+ and try again
    pause
    exit /b 1
)

REM Check if version_manager.py exists
if not exist "scripts-tools\version_manager.py" (
    echo ❌ Error: version_manager.py not found
    echo Please ensure you're running this from the correct directory
    pause
    exit /b 1
)

REM Run the Python script with all arguments
python scripts-tools\version_manager.py %*

REM If no arguments provided, show help
if "%1"=="" (
    echo.
    echo 🏷️ Version Manager for FuzeInfra Platform
    echo.
    echo Common usage examples:
    echo   version_manager.bat current
    echo   version_manager.bat bump patch
    echo   version_manager.bat bump minor --pre-release alpha
    echo   version_manager.bat bump major
    echo   version_manager.bat tag --push
    echo   version_manager.bat info
    echo   version_manager.bat validate
    echo.
    echo For full help: version_manager.bat --help
    echo.
    pause
)

endlocal