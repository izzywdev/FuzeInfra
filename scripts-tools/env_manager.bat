@echo off
REM Environment Variable Manager - Windows Batch Wrapper
REM This script provides a convenient way to run env_manager.py on Windows

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

REM Check if env_manager.py exists
if not exist "scripts-tools\env_manager.py" (
    echo ❌ Error: env_manager.py not found
    echo Please ensure you're running this from the correct directory
    pause
    exit /b 1
)

REM Run the Python script with all arguments
python scripts-tools\env_manager.py %*

REM If no arguments provided, show help
if "%1"=="" (
    echo.
    echo 🔧 Environment Variable Manager for Mendys Robot Scraper Platform
    echo.
    echo Common usage examples:
    echo   env_manager.bat add DATABASE_URL=postgresql://localhost:5432/mydb
    echo   env_manager.bat modify OPENAI_API_KEY=sk-new-key-here
    echo   env_manager.bat remove OLD_VARIABLE
    echo   env_manager.bat list
    echo   env_manager.bat backup
    echo   env_manager.bat validate
    echo   env_manager.bat list-backups
    echo.
    echo For full help: env_manager.bat --help
    echo.
    pause
)

endlocal 