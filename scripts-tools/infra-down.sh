#!/bin/bash

# Stop shared infrastructure services
echo "🛑 Stopping shared infrastructure services..."

cd infrastructure

# Stop all infrastructure services
docker-compose -f docker-compose.FuzeInfra.yml down

echo "✅ Infrastructure services stopped!"
echo ""
echo "Note: Data volumes are preserved. To remove volumes:"
echo "  docker-compose -f docker-compose.FuzeInfra.yml down -v" 
