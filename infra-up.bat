@echo off
echo 🚀 Starting shared infrastructure services...

cd infrastructure

:: Create the shared network if it doesn't exist
docker network create shared-infra 2>nul || echo Network 'shared-infra' already exists

:: Start all infrastructure services
docker-compose -f docker-compose.shared-infra.yml up -d

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