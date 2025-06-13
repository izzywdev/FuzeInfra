# FuzeInfra - Shared Infrastructure Platform

A containerized shared infrastructure platform providing common services for microservices development. This repository contains **only** generic infrastructure components - no application-specific code.

## 🚀 Quick Start

1. **Clone repository**: `git clone --recursive https://github.com/izzywdev/FuzeInfra.git`
2. **Set up environment**: `python scripts-tools/setup_environment.py`
3. **Start infrastructure**: `./infra-up.bat` (Windows) or `./infra-up.sh` (Linux/Mac)

> **Note**: Use `--recursive` to automatically clone the envmanager submodule. If already cloned, run `git submodule update --init --recursive`.

## 📁 Repository Structure

```
FuzeInfra/
├── docs/
│   ├── templates/           # Project templates for applications
│   └── PROJECT_TEMPLATES.md # How to use templates
├── scripts-tools/           # Infrastructure management tools
├── monitoring/              # Monitoring configurations
├── monitoring-shared/       # Shared monitoring configs
├── envmanager/              # Environment management submodule (git submodule)
├── docker-compose.FuzeInfra.yml # Main infrastructure services
├── environment.template     # Environment variables template
└── README.md
```

## 🔧 Environment Configuration

### First-time Setup
```bash
# Run the interactive environment setup script
python scripts-tools/setup_environment.py
```

The setup script will:
- Create a `.env` file from the template
- Generate secure passwords for all services
- Allow customization of project settings
- Provide security reminders

### Manual Setup (Alternative)
```bash
# Copy the template and edit manually
cp environment.template .env
# Edit .env with your preferred settings
```

### Key Environment Variables
- `COMPOSE_PROJECT_NAME`: Project name (default: fuzeinfra)
- `NETWORK_NAME`: Docker network name (default: FuzeInfra)
- Database credentials and ports
- Monitoring service credentials
- Security keys and tokens

See `environment.template` for all available options.

## 🏗️ Infrastructure Services

### Databases
- **PostgreSQL**: `localhost:5432` - Primary relational database
- **MongoDB**: `localhost:27017` - Document database
- **Redis**: `localhost:6379` - In-memory cache/store
- **Neo4j**: `localhost:7474` - Graph database

### Monitoring & Observability
- **Grafana**: `http://localhost:3001` - Dashboards and visualization
- **Prometheus**: `http://localhost:9090` - Metrics collection
- **Loki**: Log aggregation
- **Promtail**: Log shipping

### Message Queues
- **Kafka**: `localhost:9092` - Event streaming
- **RabbitMQ**: `localhost:5672` - Message broker

### Workflow Orchestration
- **Airflow**: `localhost:8082` - Workflow orchestration and scheduling

### Management UIs
- **Airflow UI**: http://localhost:8082 (admin/[generated])
- **Flower (Celery Monitor)**: http://localhost:5555
- **Grafana**: http://localhost:3001 (admin/[generated])
- **Prometheus**: http://localhost:9090
- **MongoDB Express**: http://localhost:8081 (admin/[generated])
- **Kafka UI**: http://localhost:8080
- **Neo4j Browser**: http://localhost:7474 (neo4j/[generated])

## 🎯 Using with Your Projects

### 1. Start Infrastructure
```bash
# Start shared infrastructure first
./infra-up.sh  # or infra-up.bat on Windows
```

### 2. Use Project Templates
Copy and customize project templates for your applications:

```bash
# Copy project startup scripts
cp docs/templates/project-up.sh.template ./your-project/project-up.sh
cp docs/templates/project-down.sh.template ./your-project/project-down.sh

# Customize for your project
# Edit PROJECT_NAME, ports, etc.
```

### 3. Connect Your Application
Your application's `docker-compose.yml` should use the external network:

```yaml
version: '3.8'

networks:
  FuzeInfra:
    external: true

services:
  your-app:
    build: .
    networks:
      - FuzeInfra
    environment:
      - POSTGRES_HOST=postgres
      - REDIS_HOST=redis
      - MONGODB_HOST=mongodb
```

See [docs/PROJECT_TEMPLATES.md](docs/PROJECT_TEMPLATES.md) for detailed instructions.

## 🛠️ Management Tools

### Infrastructure Management
- `./infra-up.sh` / `./infra-up.bat` - Start infrastructure
- `./infra-down.sh` - Stop infrastructure

### Environment Management
- `scripts-tools/env_manager.py` - Manage environment variables
- `scripts-tools/setup_environment.py` - Initial environment setup
- `envmanager/` - Advanced environment management submodule (cross-platform)

### Version Management
- `scripts-tools/version_manager.py` - Semantic versioning
- `scripts-tools/ci_cd_manager.py` - CI/CD utilities

### Health Monitoring
- `docker/app/health_check.py` - Infrastructure health checks

## 🔒 Security Notes

- **Never commit the `.env` file** - it contains sensitive credentials
- Change default passwords before production use
- Use the setup script to generate secure passwords
- Consider using secrets management in production environments
- The `.env` file is automatically added to `.gitignore`

## 🏛️ Architecture Principles

### Shared Infrastructure Only
This repository contains **only** generic infrastructure services that can be shared across multiple applications:
- Database services
- Monitoring and logging
- Message queues
- Caching layers

### Application Separation
- Application-specific code belongs in separate repositories
- Each application connects to shared infrastructure via the `FuzeInfra` network
- Use project templates to maintain consistency

### Network Architecture
All services communicate through the `FuzeInfra` Docker network, enabling:
- Service discovery by name (e.g., `postgres`, `redis`)
- Isolation from other Docker networks
- Easy scaling and management

## 📊 Monitoring

The platform includes comprehensive monitoring:
- **System metrics**: CPU, memory, disk usage
- **Service health**: Database connectivity, message queue status
- **Application metrics**: Custom metrics via Prometheus
- **Logs**: Centralized logging via Loki/Promtail
- **Dashboards**: Pre-built Grafana dashboards

## 🧪 Testing Infrastructure

The repository includes a comprehensive test suite to verify all services are running correctly:

### Quick Test Run
```bash
# Run complete test suite (recommended)
python scripts-tools/run_tests.py
```

### Manual Testing
```bash
# Install test dependencies
pip install -r tests/requirements.txt

# Create network and start services
docker network create FuzeInfra
docker-compose -f docker-compose.FuzeInfra.yml up -d

# Run tests
pytest tests/ -v

# Cleanup
docker-compose -f docker-compose.FuzeInfra.yml down -v
```

### Test Coverage
- **Database services**: PostgreSQL, MongoDB, Redis, Neo4j, Elasticsearch
- **Messaging services**: Kafka, RabbitMQ
- **Monitoring stack**: Prometheus, Grafana, Alertmanager, Loki
- **Workflow orchestration**: Airflow, Flower
- **Web interfaces**: Mongo Express, Kafka UI, RabbitMQ Management

### Continuous Integration
- Tests run automatically on push to `main` branch
- Docker images are cached in GitHub Container Registry
- Full infrastructure validation in CI/CD pipeline

See [tests/README.md](tests/README.md) for detailed testing documentation.

## 🔄 Version Management

Current version: Check `version.json`

Use the version manager for releases:
```bash
cd scripts-tools
python version_manager.py current
python version_manager.py bump patch  # or minor, major
```

## 🤝 Contributing

1. Keep infrastructure generic - no application-specific code
2. Update templates when adding new infrastructure services
3. Maintain backward compatibility
4. Update documentation for new features
5. Test with multiple projects to ensure generality

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.
