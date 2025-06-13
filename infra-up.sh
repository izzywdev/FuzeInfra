#!/bin/bash

# Start shared infrastructure services
echo "🚀 Starting shared infrastructure services..."

# Create the shared network if it doesn't exist
docker network create FuzeInfra 2>/dev/null || echo "Network 'FuzeInfra' already exists"

# Start all infrastructure services
docker-compose -f docker-compose.FuzeInfra.yml up -d

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
