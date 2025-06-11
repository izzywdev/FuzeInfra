# FuzeInfra - Shared Infrastructure Platform

A containerized shared infrastructure platform for microservices development.

## Quick Start

1. Clone repository: git clone https://github.com/izzywdev/FuzeInfra.git`n2. Create network: docker network create shared-infra`n3. Start services: ./infra-up.bat (Windows) or ./infra-up.sh (Linux/Mac)

## Access Services
- Grafana: http://localhost:3001 (admin/admin)
- Prometheus: http://localhost:9090
- MongoDB Express: http://localhost:8081 (admin/admin)
- Kafka UI: http://localhost:8080
- Neo4j: http://localhost:7474 (neo4j/password123)

## Architecture
Provides shared infrastructure services:
- Databases: PostgreSQL, MongoDB, Redis, Neo4j
- Monitoring: Grafana, Prometheus, Loki
- Message Queues: Kafka, RabbitMQ
- Workflow: Airflow

All services use shared-infra external network for cross-project communication.
