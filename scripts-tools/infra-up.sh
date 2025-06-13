#!/bin/bash

# Start shared infrastructure services
echo "🚀 Starting shared infrastructure services..."

cd infrastructure

# Create the shared network if it doesn't exist
docker network create FuzeInfra 2>/dev/null || echo "Network 'FuzeInfra' already exists"

# Start all infrastructure services
docker-compose -f docker-compose.FuzeInfra.yml up -d

# Wait for airflow-init to complete and remove it if successful
echo "⏳ Waiting for Airflow initialization to complete..."
max_wait=60
wait_time=0

while [ $wait_time -lt $max_wait ]; do
    if ! docker ps --filter "name=fuzeinfra-airflow-init" --filter "status=running" | grep -q fuzeinfra-airflow-init; then
        # airflow-init is no longer running, check its exit code
        init_exit_code=$(docker inspect fuzeinfra-airflow-init --format='{{.State.ExitCode}}' 2>/dev/null || echo "1")
        
        if [ "$init_exit_code" = "0" ]; then
            echo "✅ Airflow initialization completed successfully!"
            echo "🧹 Removing airflow-init container to clean up Docker group status..."
            docker rm fuzeinfra-airflow-init 2>/dev/null || true
            break
        else
            echo "❌ Airflow initialization failed with exit code: $init_exit_code"
            echo "📋 Check airflow-init logs:"
            docker logs fuzeinfra-airflow-init --tail=20
            break
        fi
    fi
    
    echo "   Waiting for airflow-init to complete... ($wait_time/$max_wait seconds)"
    sleep 2
    wait_time=$((wait_time + 2))
done

if [ $wait_time -ge $max_wait ]; then
    echo "⚠️  Airflow initialization is taking longer than expected"
    echo "   You can check the status with: docker logs fuzeinfra-airflow-init"
fi

echo "✅ Infrastructure services started!"
echo ""
echo "Services available at:"
echo "  📊 PostgreSQL:    localhost:5432"
echo "  🍃 MongoDB:       localhost:27017"
echo "  🔴 Redis:         localhost:6379"
echo "  📡 Kafka:         localhost:29092"
echo "  🕸️  Neo4j:        http://localhost:7474"
echo "  🔍 Elasticsearch: http://localhost:9200"
echo "  🐰 RabbitMQ:      http://localhost:15672"
echo ""
echo "To stop: ./scripts-tools/infra-down.sh" 
