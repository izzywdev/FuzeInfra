@echo off
setlocal enabledelayedexpansion
echo 🚀 Starting shared infrastructure services...

:: Create the shared network if it doesn't exist
docker network create FuzeInfra 2>nul || echo Network 'FuzeInfra' already exists

:: Start all infrastructure services
docker-compose -f docker-compose.FuzeInfra.yml up -d

:: Wait for airflow-init to complete and remove it if successful
echo ⏳ Waiting for Airflow initialization to complete...
set max_wait=60
set wait_time=0

:wait_loop
if !wait_time! geq !max_wait! goto timeout_check

:: Check if airflow-init is still running
docker ps --filter "name=fuzeinfra-airflow-init" --filter "status=running" | findstr fuzeinfra-airflow-init >nul
if errorlevel 1 (
    :: airflow-init is no longer running, check its exit code
    for /f %%i in ('docker inspect fuzeinfra-airflow-init --format="{{.State.ExitCode}}" 2^>nul ^|^| echo 1') do set init_exit_code=%%i
    
    if "!init_exit_code!"=="0" (
        echo ✅ Airflow initialization completed successfully!
        echo 🧹 Removing airflow-init container to clean up Docker group status...
        docker rm fuzeinfra-airflow-init 2>nul
        goto services_info
    ) else (
        echo ❌ Airflow initialization failed with exit code: !init_exit_code!
        echo 📋 Check airflow-init logs:
        docker logs fuzeinfra-airflow-init --tail=20
        goto services_info
    )
)

echo    Waiting for airflow-init to complete... (!wait_time!/!max_wait! seconds)
timeout /t 2 >nul
set /a wait_time+=2
goto wait_loop

:timeout_check
echo ⚠️  Airflow initialization is taking longer than expected
echo    You can check the status with: docker logs fuzeinfra-airflow-init

:services_info
echo ✅ Infrastructure services started!
echo.
echo 📊 DATABASE SERVICES:
echo   PostgreSQL:    localhost:5432
echo   MongoDB:       localhost:27017
echo   Mongo Express: http://localhost:8081
echo   Redis:         localhost:6379
echo   Neo4j:         http://localhost:7474
echo   Elasticsearch: http://localhost:9200
echo.
echo 📡 MESSAGE QUEUE SERVICES:
echo   Kafka:         localhost:29092
echo   Kafka UI:      http://localhost:8080
echo   RabbitMQ:      http://localhost:15672
echo.
echo 📈 MONITORING SERVICES:
echo   Prometheus:    http://localhost:9090
echo   Grafana:       http://localhost:3001
echo   Alertmanager:  http://localhost:9093
echo   Node Exporter: http://localhost:9100
echo.
echo 📝 LOGGING SERVICES:
echo   Loki:          http://localhost:3100
echo   Promtail:      (log shipper)
echo.
echo 🔄 WORKFLOW SERVICES:
echo   Airflow:       http://localhost:8082
echo   Flower:        http://localhost:5555
echo.
echo To stop: scripts-tools\infra-down.bat 
