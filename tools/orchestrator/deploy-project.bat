@echo off
setlocal enabledelayedexpansion

REM Local Development Orchestrator - Project Deployment Script (Windows)
REM Usage: deploy-project.bat <project-name> [project-path] [compose-file]

set "PROJECT_NAME=%1"
if "%PROJECT_NAME%"=="" (
    echo Usage: %0 ^<project-name^> [project-path] [compose-file]
    echo Example: %0 sportsbuck ..\projects\sportsbuck
    echo Example: %0 sportsbuck ..\projects\sportsbuck docker-compose.dev.yml
    exit /b 1
)

set "PROJECT_PATH=%2"
if "%PROJECT_PATH%"=="" (
    set "PROJECT_PATH=..\..\projects\%PROJECT_NAME%"
)

set "COMPOSE_FILE=%3"
if "%COMPOSE_FILE%"=="" (
    set "COMPOSE_FILE=docker-compose.yml"
)

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%..\..\"
set "TOOLS_DIR=%PROJECT_ROOT%tools"

echo 🚀 Deploying %PROJECT_NAME%...
echo 📁 Project path: %PROJECT_PATH%
echo.

REM Check requirements
echo ℹ️ Checking requirements...

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 3 is required but not installed
    exit /b 1
)

REM Check Docker
docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not running or not accessible
    exit /b 1
)

REM Check FuzeInfra network
docker network ls | findstr "FuzeInfra" >nul
if errorlevel 1 (
    echo ⚠️ FuzeInfra network not found, creating it...
    docker network create FuzeInfra
)

echo ✅ Requirements check passed

REM Validate project
echo ℹ️ Validating project configuration...

if not exist "%PROJECT_PATH%" (
    echo ❌ Project directory does not exist: %PROJECT_PATH%
    exit /b 1
)

if not exist "%PROJECT_PATH%\%COMPOSE_FILE%" (
    echo ❌ No %COMPOSE_FILE% found in project directory
    exit /b 1
)

echo ✅ Project configuration will be inferred from %COMPOSE_FILE%

REM Allocate ports
echo ℹ️ Analyzing project and allocating ports for %PROJECT_NAME%...
python "%TOOLS_DIR%\port-allocator\port-allocator.py" allocate %PROJECT_NAME% --compose-file "%PROJECT_PATH%\%COMPOSE_FILE%" > temp_ports.json
if errorlevel 1 (
    echo ❌ Failed to allocate ports
    exit /b 1
)

for /f "delims=" %%i in (temp_ports.json) do set "PORT_ALLOCATION=%%i"

REM Inject environment variables
echo ℹ️ Injecting environment variables...
python "%TOOLS_DIR%\env-manager\env-injector.py" inject "%PROJECT_PATH%" --ports "!PORT_ALLOCATION!" > temp_env.json
if errorlevel 1 (
    echo ❌ Failed to inject environment variables
    del temp_ports.json
    exit /b 1
)

REM Set environment variables from allocation
echo ℹ️ Setting up environment variables...
for /f "tokens=*" %%i in ('python -c "import json, sys; data=json.load(sys.stdin); [print(f'SET {k}={v}') for k,v in data['port_mapping'].items() if v]" ^< temp_ports.json') do %%i

REM Generate nginx config
echo ℹ️ Generating nginx configuration...
python "%TOOLS_DIR%\nginx-generator\nginx-generator.py" generate --project-name %PROJECT_NAME% > temp_nginx.json
if errorlevel 1 (
    echo ❌ Failed to generate nginx configuration
    del temp_ports.json temp_env.json
    exit /b 1
)

REM Update DNS
echo ℹ️ Updating DNS routing for %PROJECT_NAME%.dev.local...
python "%TOOLS_DIR%\dns-manager\dns-manager.py" add %PROJECT_NAME% > temp_dns.json
if errorlevel 1 (
    echo ⚠️ Failed to update DNS routing (may require admin privileges)
    echo ℹ️ You can manually add this entry to your hosts file:
    echo ℹ️ 127.0.0.1    %PROJECT_NAME%.dev.local
)

REM Start shared nginx
echo ℹ️ Starting shared nginx if not running...
docker ps | findstr "fuzeinfra-shared-nginx" >nul
if errorlevel 1 (
    echo ℹ️ Starting shared nginx container...
    cd /d "%PROJECT_ROOT%\infrastructure\shared-nginx"
    docker-compose up -d
    timeout /t 5 /nobreak >nul
    echo ✅ Shared nginx started
) else (
    echo ℹ️ Shared nginx already running
)

REM Reload nginx
echo ℹ️ Reloading nginx configuration...
python "%TOOLS_DIR%\nginx-generator\nginx-generator.py" reload > temp_reload.json

REM Start project
echo ℹ️ Starting project containers using %COMPOSE_FILE%...
cd /d "%PROJECT_PATH%"
set "COMPOSE_PROJECT_NAME=%PROJECT_NAME%"
docker-compose -f "%COMPOSE_FILE%" up -d
if errorlevel 1 (
    echo ❌ Failed to start project containers
    cd /d "%SCRIPT_DIR%"
    del temp_*.json
    exit /b 1
)

REM Health check
echo ℹ️ Performing health checks...
timeout /t 10 /nobreak >nul

curl -s -f "http://%PROJECT_NAME%.dev.local/nginx-health" >nul 2>&1
if errorlevel 1 (
    echo ⚠️ Nginx health check failed - may need more time to start
) else (
    echo ✅ Nginx health check passed
)

REM Success message
echo.
echo ✅ 🎉 Deployment completed successfully!
echo.
echo 📊 Deployment Summary:
echo    Project Name: %PROJECT_NAME%
echo    Project Path: %PROJECT_PATH%
echo    Compose File: %COMPOSE_FILE%
echo    Access URL: http://%PROJECT_NAME%.dev.local
echo.
echo 🌐 Your application is now accessible at:
echo    http://%PROJECT_NAME%.dev.local
echo.
echo 📋 Next steps:
echo    • Access your application using the URL above
echo    • Check logs: docker-compose -f %PROJECT_PATH%\%COMPOSE_FILE% logs
echo    • Stop project: cd %PROJECT_PATH% && docker-compose -f %COMPOSE_FILE% down
echo.

REM Cleanup temp files
cd /d "%SCRIPT_DIR%"
del temp_*.json 2>nul

exit /b 0 