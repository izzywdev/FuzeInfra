#!/bin/bash

# Stop project-specific services
echo "🛑 Stopping Robot Scraper project services..."

# Stop project services
docker-compose -f docker-compose.project.yml down

echo "✅ Project services stopped!"
echo ""
echo "Infrastructure services are still running."
echo "To stop infrastructure: ./scripts-tools/infra-down.sh" 