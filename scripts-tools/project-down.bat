@echo off
echo 🛑 Stopping Robot Scraper project services...

:: Stop robot project services
cd infrastructure
docker-compose -f docker-compose.robot-project.yml down

echo ✅ Robot Scraper project services stopped!
echo.
echo Infrastructure services are still running.
echo To stop infrastructure: scripts-tools\infra-down.bat 