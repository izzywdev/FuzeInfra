# Project Templates

This directory contains generic templates for setting up projects that use the FuzeInfra shared infrastructure.

## Available Templates

### Project Startup/Shutdown Scripts

- `project-up.sh.template` - Linux/Mac project startup script
- `project-down.sh.template` - Linux/Mac project shutdown script  
- `project-up.bat.template` - Windows project startup script
- `project-down.bat.template` - Windows project shutdown script

### Database Management

- `manage_db.py.template` - Alembic-based database migration management

## How to Use Templates

### 1. Project Scripts

Copy the appropriate startup/shutdown scripts to your project root:

```bash
# For Linux/Mac projects
cp docs/templates/project-up.sh.template ./project-up.sh
cp docs/templates/project-down.sh.template ./project-down.sh
chmod +x project-up.sh project-down.sh

# For Windows projects  
cp docs/templates/project-up.bat.template ./project-up.bat
cp docs/templates/project-down.bat.template ./project-down.bat
```

### 2. Customize for Your Project

Edit the copied files and update these variables:

```bash
PROJECT_NAME="your-actual-project-name"
COMPOSE_FILE="docker-compose.yml"  # or your compose file name
BACKEND_PORT="8080"  # your backend port
FRONTEND_PORT="3000"  # your frontend port
```

### 3. Database Management (Optional)

If your project uses PostgreSQL with Alembic migrations:

```bash
# Copy the template
cp docs/templates/manage_db.py.template ./scripts/manage_db.py

# Install dependencies
pip install alembic sqlalchemy psycopg2-binary

# Initialize Alembic in your project
alembic init alembic

# Update environment variables in the script for your database
```

## Prerequisites

### Infrastructure Must Be Running

Before starting any project, ensure the FuzeInfra shared infrastructure is running:

```bash
# Start shared infrastructure
./infra-up.sh  # or infra-up.bat on Windows
```

### Docker Compose File

Your project needs a `docker-compose.yml` file that:

1. Uses the `FuzeInfra` external network
2. Defines your application services
3. Connects to shared infrastructure services

Example docker-compose.yml structure:

```yaml
version: '3.8'

networks:
  FuzeInfra:
    external: true

services:
  your-backend:
    build: .
    ports:
      - "8080:8080"
    networks:
      - FuzeInfra
    environment:
      - POSTGRES_HOST=postgres
      - REDIS_HOST=redis
      - MONGODB_HOST=mongodb
    depends_on:
      - postgres
      - redis
      - mongodb

  your-frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    networks:
      - FuzeInfra
```

## Shared Infrastructure Services

Your project can connect to these shared services:

- **PostgreSQL**: `postgres:5432`
- **MongoDB**: `mongodb:27017`  
- **Redis**: `redis:6379`
- **Kafka**: `kafka:9092`
- **Elasticsearch**: `elasticsearch:9200`
- **Prometheus**: `prometheus:9090`
- **Grafana**: `grafana:3001`

## Environment Variables

Use the shared environment variables from the FuzeInfra `.env` file, or define project-specific ones in your own `.env` file.

## Example Project Setup

```bash
# 1. Ensure infrastructure is running
./infra-up.sh

# 2. Copy project templates
cp docs/templates/project-up.sh.template ./project-up.sh
cp docs/templates/project-down.sh.template ./project-down.sh
chmod +x *.sh

# 3. Customize project scripts
# Edit PROJECT_NAME, ports, etc. in the scripts

# 4. Create your docker-compose.yml
# Define your services using FuzeInfra network

# 5. Start your project
./project-up.sh
```

This approach ensures clean separation between shared infrastructure and project-specific services while maintaining consistency across all projects. 