# FuzeInfra Test Suite

This directory contains comprehensive tests for the FuzeInfra shared infrastructure platform. The test suite verifies that all infrastructure services are running correctly and properly configured.

## Test Coverage

### Database Services
- **PostgreSQL** (`test_databases.py`)
  - Connection and basic operations
  - Airflow database setup
- **MongoDB** (`test_databases.py`)
  - Connection and CRUD operations
  - Admin access verification
- **Redis** (`test_databases.py`)
  - Connection and basic operations
  - Data structures (lists, hashes)
- **Neo4j** (`test_databases.py`)
  - Connection and graph operations
  - APOC plugin availability
- **Elasticsearch** (`test_databases.py`)
  - Connection and cluster info
  - Indexing and search operations

### Messaging Services
- **Apache Kafka** (`test_messaging.py`)
  - Producer/consumer functionality
  - JSON message handling
- **RabbitMQ** (`test_messaging.py`)
  - Queue operations
  - Exchange routing
  - JSON message handling

### Monitoring & Observability
- **Prometheus** (`test_monitoring.py`)
  - Health and readiness checks
  - Configuration and targets
  - Metrics queries
- **Grafana** (`test_monitoring.py`)
  - Health checks
  - Datasource configuration
  - Dashboard availability
- **Alertmanager** (`test_monitoring.py`)
  - Health and configuration
  - Alerts endpoint
- **Loki** (`test_monitoring.py`)
  - Health checks
  - Log ingestion and querying
- **Node Exporter** (`test_monitoring.py`)
  - System metrics availability

### Workflow Orchestration
- **Apache Airflow** (`test_airflow.py`)
  - Webserver health
  - API endpoints
  - Configuration access
- **Flower (Celery Monitor)** (`test_airflow.py`)
  - Dashboard accessibility
  - Workers and tasks API
- **Airflow Workflow Execution** (`test_airflow_workflows.py`)
  - DAG triggering and execution
  - Task execution verification
  - Dependency management
  - Parallel task processing
  - Celery worker functionality
  - Task routing and queues

### Web Interfaces
- **Mongo Express** (`test_web_interfaces.py`)
  - Authentication and access
- **Kafka UI** (`test_web_interfaces.py`)
  - Dashboard and API endpoints
- **RabbitMQ Management** (`test_web_interfaces.py`)
  - Management interface and API
- **Neo4j Browser** (`test_web_interfaces.py`)
  - Browser interface access

## Running Tests

### Prerequisites

1. **Docker and Docker Compose** must be installed and running
2. **Python 3.8+** with pip
3. **FuzeInfra network** must be created: `docker network create FuzeInfra`

### Install Dependencies

```bash
pip install -r tests/requirements.txt
```

### Run All Tests

#### Using the Test Runner (Recommended)
```bash
python scripts-tools/run_tests.py
```

This script will:
- Check prerequisites
- Install dependencies
- Create the Docker network
- Start all infrastructure services
- Wait for services to be ready
- Run the complete test suite
- Clean up resources

#### Manual Testing
```bash
# Start infrastructure
docker-compose -f docker-compose.FuzeInfra.yml up -d

# Wait for services (60+ seconds)
sleep 60

# Run tests
pytest tests/ -v

# Cleanup
docker-compose -f docker-compose.FuzeInfra.yml down -v
```

### Run Specific Test Categories

```bash
# Database tests only
pytest tests/test_databases.py -v

# Monitoring tests only
pytest tests/test_monitoring.py -v

# Messaging tests only
pytest tests/test_messaging.py -v

# Airflow tests only
pytest tests/test_airflow.py -v

# Airflow workflow execution tests only
pytest tests/test_airflow_workflows.py -v

# Web interface tests only
pytest tests/test_web_interfaces.py -v
```

### Run Tests with Markers

```bash
# Run only integration tests
pytest tests/ -m integration -v

# Skip slow tests
pytest tests/ -m "not slow" -v
```

## Test Configuration

### Pytest Configuration
- Configuration is in `pytest.ini` at the project root
- Test discovery patterns and markers are defined
- Warnings are filtered for cleaner output

### Service URLs
- All service URLs are defined in `conftest.py`
- Tests use fixtures to access service connections
- Timeouts and retry logic are built-in

### Environment Variables
- Tests use the same `.env` file as the infrastructure
- No additional configuration required

## Continuous Integration

### GitHub Actions
The test suite runs automatically on:
- Push to `main` or `develop` branches
- Pull requests to `main`

The workflow (`..github/workflows/infrastructure-tests.yml`):
- Sets up Python and Docker
- Caches Docker images in GitHub Container Registry
- Starts the complete infrastructure
- Runs all tests
- Publishes test results
- Cleans up resources

### Docker Image Caching
- All Docker images are cached in GitHub Container Registry
- Images are tagged with `ghcr.io/[repository]/[service]:[tag]`
- Cached images are available for local development

## Troubleshooting

### Common Issues

1. **Services not ready**: Increase wait time in test runner
2. **Port conflicts**: Ensure no other services are using the same ports
3. **Docker network issues**: Recreate the FuzeInfra network
4. **Permission errors**: Ensure Docker daemon is running with proper permissions

### Debug Mode

```bash
# Run with verbose output and no capture
pytest tests/ -v -s --tb=long

# Run specific test with debugging
pytest tests/test_databases.py::TestPostgreSQL::test_postgres_connection -v -s
```

### Service Logs

```bash
# View all service logs
docker-compose -f docker-compose.FuzeInfra.yml logs

# View specific service logs
docker-compose -f docker-compose.FuzeInfra.yml logs postgres
docker-compose -f docker-compose.FuzeInfra.yml logs prometheus
```

## Contributing

When adding new infrastructure services:

1. Add service configuration to `docker-compose.FuzeInfra.yml`
2. Create appropriate test cases in the relevant test file
3. Add service URL to `conftest.py` if it has a web interface
4. Update this README with the new service coverage
5. Add the Docker image to the GitHub workflow caching

### Test Guidelines

- Each service should have connection/health tests
- Test basic functionality, not exhaustive features
- Use appropriate timeouts for service startup
- Clean up test data after each test
- Use descriptive test names and docstrings

## Test DAGs

The test suite includes sample DAGs in `airflow-shared/dags/` for testing workflow functionality:

### `test_workflow_simple.py`
- **Purpose**: Automated testing of Airflow and Celery functionality
- **Tasks**: 
  - Simple Python task (basic execution)
  - CPU-intensive task (worker processing)
  - Memory allocation task (resource handling)
  - Bash task (command execution)
  - Dependency task (workflow orchestration)
- **Features**: Task dependencies, parallel execution, resource testing

### `example_shared_infrastructure_dag.py`
- **Purpose**: Demonstrates usage of shared infrastructure services
- **Tasks**: PostgreSQL, Redis, MongoDB, Elasticsearch connections
- **Features**: Infrastructure integration, service connectivity testing

These DAGs are automatically loaded by Airflow and can be triggered manually or through the test suite. 