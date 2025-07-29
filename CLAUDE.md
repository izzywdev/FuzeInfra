# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

FuzeInfra is a comprehensive containerized shared infrastructure platform that provides common services for microservices development. It contains **only** generic infrastructure components with no application-specific code, following a clean separation pattern where applications connect via the shared `FuzeInfra` Docker network.

## Essential Development Commands

### Infrastructure Management
```bash
# First-time setup
python scripts-tools/setup_environment.py  # Interactive environment configuration
./tools/cert-manager/setup-local-certs.sh  # Generate local HTTPS certificates
./infra-up.sh                              # Start all infrastructure (Linux/macOS)
./infra-up.bat                             # Start all infrastructure (Windows) 
./infra-down.sh                            # Stop all infrastructure

# Environment management
python scripts-tools/env_manager.py list                # List current environment variables
python scripts-tools/env_manager.py add VAR=value       # Add new variable
python scripts-tools/env_manager.py modify VAR=newvalue # Modify existing variable

# Version management
python scripts-tools/version_manager.py current         # Show current version
python scripts-tools/version_manager.py bump patch|minor|major  # Bump version

# DNS and networking
python tools/dns-manager/dns-manager.py status          # Check DNS server status
python tools/dns-manager/dns-manager.py add myproject   # Add project DNS entry

# Tunnel and webhook management
python tools/tunnel-manager/tunnel-manager.py status    # Check tunnel status
python tools/tunnel-manager/webhook_sync.py monitor     # Start webhook monitoring
```

### Testing
```bash
# Comprehensive automated testing
python scripts-tools/run_tests.py          # Run complete test suite

# Manual testing with pytest
pip install -r tests/requirements.txt
pytest tests/ -v                           # All tests
pytest tests/test_databases.py -v          # Database tests only
pytest tests/test_monitoring.py -v         # Monitoring tests only
pytest tests/ -m integration -v            # Integration tests only

# Certificate testing
curl -k https://grafana.dev.local/api/health      # Test HTTPS endpoints
curl -k https://prometheus.dev.local/-/healthy    # Test Prometheus HTTPS
```

## Architecture Overview

### Core Infrastructure Services
FuzeInfra provides a complete infrastructure stack via Docker Compose:

**Database Services:**
- PostgreSQL (port 5432) - Primary relational database
- MongoDB (port 27017) - Document database with Mongo Express UI
- Redis (port 6379) - In-memory cache/store with persistence
- Neo4j (ports 7474/7687) - Graph database with APOC plugins
- Elasticsearch (port 9200) - Search and analytics engine
- ChromaDB (port 8003) - Vector database for AI/ML applications

**Message Queue Services:**
- Apache Kafka (port 29092) - Event streaming with Zookeeper
- RabbitMQ (ports 5672/15672) - Message broker with management UI

**DNS & Network Services:**
- dnsmasq (port 53) - Local DNS server with wildcard `*.dev.local` support
- Web UI (port 8053) - DNS management interface

**Monitoring Stack:**
- Prometheus (port 9090) - Metrics collection
- Grafana (port 3001) - Dashboards with pre-configured datasources
- Alertmanager (port 9093) - Alert handling
- Loki (port 3100) - Log aggregation
- Promtail - Log shipping agent
- Node Exporter (port 9100) - System metrics

**Workflow Orchestration:**
- Apache Airflow (port 8082) - Complete workflow orchestration
- Flower (port 5555) - Celery monitoring interface

### Local Development Orchestrator

The platform includes an advanced orchestrator system that eliminates port conflicts and provides clean development URLs:

**Features:**
- Automatic port allocation to prevent conflicts
- Clean URLs via `http://projectname.dev.local` instead of `localhost:PORT`
- Automatic .env file generation with port injection
- Centralized nginx proxy with automatic configuration
- DNS management via dnsmasq server with wildcard domain support

**Orchestrator Components:**
- `tools/port-allocator/` - Port discovery and allocation
- `tools/env-manager/` - Environment variable injection
- `tools/nginx-generator/` - Nginx configuration generation
- `tools/dns-manager/` - Local DNS management with dnsmasq integration

### DNS Server Integration

FuzeInfra includes a **dnsmasq** DNS server for seamless local domain resolution:

**DNS Features:**
- **Wildcard Support**: All `*.dev.local` domains automatically resolve to 127.0.0.1
- **Service Discovery**: Direct container access via `postgres.dev.local`, `redis.dev.local`, etc.
- **Web Interface**: Management UI available at `http://localhost:8053`
- **Fallback Support**: Automatic fallback to hosts file modification if dnsmasq unavailable
- **Dynamic Management**: Add/remove domains programmatically via `dns-manager.py`

**DNS Management Commands:**
```bash
# Check DNS server status
python tools/dns-manager/dns-manager.py status

# Add project domain
python tools/dns-manager/dns-manager.py add myproject
# Creates: myproject.dev.local -> 127.0.0.1

# Remove project domain
python tools/dns-manager/dns-manager.py remove myproject

# Custom domain and IP
python tools/dns-manager/dns-manager.py add myapp --domain-suffix custom.local --ip 192.168.1.100
```

**System DNS Configuration:**
To enable `.dev.local` domain resolution system-wide, configure your system DNS:
- **Windows**: Add `127.0.0.1` as primary DNS in network adapter settings
- **macOS/Linux**: Add `nameserver 127.0.0.1` to `/etc/resolv.conf` or network manager
- **Docker**: Containers automatically use the dnsmasq server via Docker network

## Cloudflare Tunnel Integration

FuzeInfra includes **Cloudflare Tunnel** for secure external access to local services and webhook endpoints:

**Tunnel Features:**
- **Persistent URLs**: Named tunnels maintain URLs across restarts (unlike ngrok's random URLs)
- **Zero Configuration**: Automatic service discovery and routing
- **Webhook Endpoints**: Dedicated webhook URLs for GitHub, Atlassian, OAuth callbacks
- **Auto-Update**: Automatically updates webhook URLs in external services when changed
- **Health Monitoring**: Continuous tunnel health monitoring and recovery

**Tunnel Manager Commands:**
```bash
# Check tunnel and webhook status
python tools/tunnel-manager/tunnel-manager.py status

# Register webhook for automatic management
python tools/tunnel-manager/tunnel-manager.py register myproject github --type github
python tools/tunnel-manager/tunnel-manager.py register myproject atlassian --type atlassian

# Generate webhook URLs
python tools/tunnel-manager/tunnel-manager.py url myproject github --type github
# Returns: https://myproject.webhook.your-domain.com/github

# List all registered webhooks
python tools/tunnel-manager/tunnel-manager.py list

# Sync all webhooks (update URLs in external services)
python tools/tunnel-manager/webhook_sync.py sync

# Start continuous monitoring and auto-sync
python tools/tunnel-manager/webhook_sync.py monitor
```

**Supported Webhook Auto-Updates:**
- **GitHub**: Full API automation for webhook URL updates
- **Atlassian (Jira/Confluence)**: Full API automation for webhook URL updates
- **Google OAuth**: Manual update notifications (no API available)
- **Custom Services**: Configurable via API integrations

**Setup Requirements:**
1. **Cloudflare Account**: Create tunnel at [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. **Domain**: Configure domain in Cloudflare DNS
3. **Environment Variables**: Set `CLOUDFLARE_TUNNEL_TOKEN` and related config
4. **API Tokens**: Configure `GITHUB_TOKEN`, `ATLASSIAN_API_TOKEN` for auto-updates

## Certificate Management

FuzeInfra includes **mkcert** integration for locally-trusted HTTPS certificates:

**Certificate Features:**
- **Locally Trusted**: No browser security warnings
- **Wildcard Support**: `*.dev.local` certificates for all services
- **Auto-Generation**: One command creates all needed certificates
- **Nginx Integration**: Automatic HTTPS configuration for all services

**Certificate Setup Commands:**
```bash
# Generate local certificates (one-time setup)
./tools/cert-manager/setup-local-certs.sh

# Restart nginx to load certificates
docker-compose -f docker-compose.FuzeInfra.yml restart nginx

# Test HTTPS access
curl https://grafana.dev.local
curl https://prometheus.dev.local
```

**Available Certificate Types:**
- **mkcert** (recommended): Locally trusted certificates with wildcard support
- **Let's Encrypt**: Production certificates for real domains
- **Self-signed**: Basic certificates (shows browser warnings)

## Skaffold Integration (Optional)

For enhanced development workflow, FuzeInfra supports **Skaffold** integration:

**Skaffold Benefits:**
- **Hot Reload**: Instant code updates without container rebuilds
- **Multi-Project**: Orchestrate multiple applications simultaneously
- **Smart Builds**: Only rebuild changed services
- **Port Forwarding**: Automatic port management
- **Profile Support**: Different configurations for dev/staging/production

**Skaffold Commands:**
```bash
# Install Skaffold
curl -Lo skaffold https://storage.googleapis.com/skaffold/releases/latest/skaffold-linux-amd64
sudo install skaffold /usr/local/bin/

# Start development with hot reload
skaffold dev

# Infrastructure only
skaffold run --profile infrastructure-only

# With HTTPS enabled
ENABLE_HTTPS=true skaffold dev
```

### Application Integration Pattern

Applications connect to FuzeInfra using Docker external networks:

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

Services are accessible by their container names within the FuzeInfra network, enabling seamless service discovery.

## Configuration Management

### Environment Setup
- **Template-based Configuration:** `environment.template` provides all available configuration options
- **Interactive Setup:** `setup_environment.py` provides guided configuration with secure password generation
- **Validation:** Built-in environment validation and security checks
- **Backup System:** Automatic `.env` file backups before changes

### Key Configuration Areas
- Network configuration (compose project name, network name)
- Database credentials and ports for all services
- Monitoring service credentials and alerting configuration
- Security keys and tokens (JWT, Fernet keys for Airflow)
- External service configurations (SMTP, AWS, etc.)

## Project Templates and Standards

The platform provides standardized templates for consistent project integration:
- Cross-platform startup/shutdown scripts (`project-up.sh`, `project-up.bat`)
- Database management templates (`manage_db.py.template`)
- Environment configuration templates with port injection
- Docker Compose integration patterns

## Testing Framework

### Test Categories
- **Database Tests:** Connection, health checks, data persistence
- **Monitoring Tests:** Prometheus metrics, Grafana dashboards, alerting
- **Messaging Tests:** Kafka/RabbitMQ connectivity and message flow
- **Workflow Tests:** Airflow DAG validation and execution
- **Web Interface Tests:** UI accessibility and functionality

### Test Configuration
- Tests use `pytest` with comprehensive fixtures in `conftest.py`
- Integration tests marked with `@pytest.mark.integration`
- Service health checks with retry logic for container startup delays
- Automated CI/CD testing via GitHub Actions

## Key File Locations

### Core Configuration
- `docker-compose.FuzeInfra.yml` - Main infrastructure orchestration
- `environment.template` - Complete environment variable template
- `version.json` - Version tracking and build information
- `pytest.ini` - Test configuration and markers

### Management Scripts
- `scripts-tools/` - Complete infrastructure management utilities
- `infra-up.sh` / `infra-up.bat` - Cross-platform startup scripts
- `infra-down.sh` - Infrastructure shutdown script

### Monitoring Configuration
- `monitoring-shared/` - Shared monitoring configurations (Prometheus, Grafana, Loki)
- `infrastructure/nginx.conf` - Nginx reverse proxy configuration
- `docker/` - Service-specific configurations and init scripts

### Development Tools
- `tools/` - Local development orchestrator components
- `docs/` - Project templates and implementation documentation
- `tests/` - Comprehensive test suite for all infrastructure components

## Security Features
- Secure password generation for all services
- Environment variable validation and masking
- No hardcoded credentials in configurations
- Health checks for all critical services
- Proper container user permissions and security headers

## Project Integration Guide

### For Developers Building Applications with FuzeInfra

#### **Quick Start Integration**

1. **Connect to FuzeInfra Network**:
```yaml
# In your project's docker-compose.yml
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
      - DATABASE_URL=postgresql://fuzeinfra:password@postgres:5432/fuzeinfra_db
      - REDIS_URL=redis://redis:6379
      - MONGODB_URL=mongodb://admin:admin123@mongodb:27017
    depends_on:
      - postgres
      - redis
```

2. **Setup Project DNS and HTTPS**:
```bash
# Add your project to local DNS
python ../FuzeInfra/tools/dns-manager/dns-manager.py add myproject

# Register webhook endpoints (if needed)
python ../FuzeInfra/tools/tunnel-manager/tunnel-manager.py register myproject github --type github

# Your app is now available at:
# - http://myproject.dev.local
# - https://myproject.dev.local (with certificates)
# - https://myproject.webhook.your-domain.com/github (tunnel endpoint)
```

3. **Environment Configuration**:
```bash
# Copy FuzeInfra environment template
cp ../FuzeInfra/environment.template .env.fuzeinfra

# Create your project-specific environment
cat > .env << EOF
# Your project configuration
NODE_ENV=development
PORT=3000
API_URL=http://myproject.dev.local:3000

# FuzeInfra service connections
DATABASE_URL=postgresql://fuzeinfra:fuzeinfra_secure_password@postgres:5432/fuzeinfra_db
REDIS_URL=redis://redis:6379
MONGODB_URL=mongodb://admin:mongodb_secure_password@mongodb:27017
ELASTICSEARCH_URL=http://elasticsearch:9200
NEO4J_URL=bolt://neo4j:7687
RABBITMQ_URL=amqp://admin:admin123@rabbitmq:5672
KAFKA_BROKER=kafka:29092

# Monitoring endpoints
PROMETHEUS_URL=http://prometheus:9090
GRAFANA_URL=http://grafana:3000
EOF
```

#### **Development Workflow**

1. **Start FuzeInfra** (once per development session):
```bash
cd path/to/FuzeInfra
./infra-up.sh
```

2. **Develop Your Application**:
```bash
cd your-project/
docker-compose up -d  # Your app connects to FuzeInfra automatically
```

3. **Access Your Services**:
- **Local Development**: `http://myproject.dev.local`
- **HTTPS**: `https://myproject.dev.local` 
- **Monitoring**: `https://grafana.dev.local`
- **Database Admin**: `https://mongo-express.dev.local`

#### **Service Connection Examples**

**Node.js/Express**:
```javascript
// Database connections
const { Pool } = require('pg');
const redis = require('redis');
const { MongoClient } = require('mongodb');

const pgPool = new Pool({
  connectionString: process.env.DATABASE_URL
});

const redisClient = redis.createClient({
  url: process.env.REDIS_URL
});

const mongoClient = new MongoClient(process.env.MONGODB_URL);
```

**Python/FastAPI**:
```python
# requirements.txt additions
psycopg2-binary
redis
pymongo
elasticsearch

# Database connections
import psycopg2
import redis
from pymongo import MongoClient
from elasticsearch import Elasticsearch

# Connect to FuzeInfra services
pg_conn = psycopg2.connect(os.getenv('DATABASE_URL'))
redis_client = redis.from_url(os.getenv('REDIS_URL'))
mongo_client = MongoClient(os.getenv('MONGODB_URL'))
es_client = Elasticsearch([os.getenv('ELASTICSEARCH_URL')])
```

**React/Frontend**:
```javascript
// API configuration
const API_CONFIG = {
  baseURL: process.env.REACT_APP_API_URL || 'http://myproject.dev.local:3000',
  timeout: 10000,
};

// WebSocket connection (if using)
const wsUrl = process.env.REACT_APP_WS_URL || 'ws://myproject.dev.local:3000';
```

#### **Webhook Integration**

For applications requiring webhooks (GitHub, Atlassian, OAuth):

```bash
# Register your project for webhook management
python ../FuzeInfra/tools/tunnel-manager/tunnel-manager.py register myproject github \
  --type github --external-id webhook-id-from-github

# The tunnel manager will automatically update webhook URLs when they change
# Your webhook endpoint: https://myproject.webhook.your-domain.com/github

# Handle webhooks in your application
curl -X POST https://myproject.webhook.your-domain.com/github \
  -H "Content-Type: application/json" \
  -d '{"action": "opened", "pull_request": {...}}'
```

#### **Testing with FuzeInfra**

```bash
# Integration tests with real services
export TEST_DATABASE_URL=postgresql://fuzeinfra:password@localhost:5432/test_db
export TEST_REDIS_URL=redis://localhost:6379/1

# Run tests against FuzeInfra services
npm test
pytest tests/integration/
```

#### **Troubleshooting**

**Common Issues**:

1. **Cannot connect to services**:
```bash
# Check FuzeInfra is running
docker ps | grep fuzeinfra

# Verify network connection
docker network ls | grep FuzeInfra
```

2. **DNS resolution not working**:
```bash
# Check DNS manager status
python ../FuzeInfra/tools/dns-manager/dns-manager.py status

# Add your project manually
python ../FuzeInfra/tools/dns-manager/dns-manager.py add myproject
```

3. **HTTPS certificates not trusted**:
```bash
# Regenerate certificates
cd ../FuzeInfra
./tools/cert-manager/setup-local-certs.sh

# Restart nginx
docker-compose -f docker-compose.FuzeInfra.yml restart nginx
```

#### **Best Practices**

1. **Resource Naming**: Use consistent naming like `myproject-api`, `myproject-frontend`
2. **Environment Variables**: Always use environment variables for service connections
3. **Health Checks**: Implement `/health` endpoints for monitoring
4. **Logging**: Use structured logging compatible with Loki
5. **Metrics**: Export Prometheus metrics on `/metrics` endpoint
6. **Graceful Shutdown**: Handle SIGTERM for clean container stops