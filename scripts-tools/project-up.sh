#!/bin/bash

# Start project-specific services
echo "🚀 Starting Robot Scraper project services..."

# Check if shared infrastructure is running
if ! docker network ls | grep -q "shared-infra"; then
    echo "❌ Shared infrastructure network not found!"
    echo "Please start infrastructure first: ./scripts-tools/infra-up.sh"
    exit 1
fi

# Start project services
docker-compose -f docker-compose.project.yml up -d

echo "✅ Project services started!"
echo ""
echo "Services available at:"
echo "  🤖 Backend API:   http://localhost:8090"
echo "  🎨 Frontend:      http://localhost:3000"
echo "  📊 Prometheus:    http://localhost:9090"
echo "  📈 Grafana:       http://localhost:3001"
echo ""
echo "To stop: ./scripts-tools/project-down.sh" 