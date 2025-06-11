@echo off
echo 🚀 Starting Robot Scraper project services...

:: Check if shared infrastructure is running
docker network ls | findstr "shared-infra" >nul
if errorlevel 1 (
    echo ❌ Shared infrastructure network not found!
    echo Please start infrastructure first: scripts-tools\infra-up.bat
    exit /b 1
)

:: Start robot project services
cd infrastructure
docker-compose -f docker-compose.robot-project.yml up -d

echo ✅ Robot Scraper project services started!
echo.
echo 🤖 PROJECT SERVICES:
echo   Backend API:           http://localhost:8090
echo   Frontend:              http://localhost:3000
echo   Unified Kafka Processor: (background service)
echo.
echo 📊 SHARED INFRASTRUCTURE ACCESS:
echo   MongoDB (via shared):  localhost:27017
echo   Kafka (via shared):    localhost:29092
echo   Grafana (via shared):  http://localhost:3001
echo   Prometheus (via shared): http://localhost:9090
echo.
echo To stop: scripts-tools\project-down.bat 