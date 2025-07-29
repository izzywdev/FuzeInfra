# FuzeInfra Project Integration Guide

This guide provides comprehensive instructions for developers building applications that integrate with FuzeInfra's shared infrastructure platform.

## Overview

FuzeInfra provides a complete development infrastructure including databases, monitoring, networking, and deployment tools. Your applications can leverage these shared services while maintaining separation of concerns.

## Prerequisites

- Docker and Docker Compose installed
- FuzeInfra running (`./infra-up.sh`)
- Basic understanding of containerized applications

## Integration Patterns

### 1. **Basic Docker Compose Integration**

Create a `docker-compose.yml` in your project root:

```yaml
version: '3.8'

# Connect to FuzeInfra shared network
networks:
  FuzeInfra:
    external: true

services:
  # Your application service
  api:
    build: 
      context: .
      dockerfile: Dockerfile
    container_name: ${PROJECT_NAME:-myproject}-api
    ports:
      - "${API_PORT:-3000}:3000"
    environment:
      # Application configuration
      NODE_ENV: ${NODE_ENV:-development}
      PORT: 3000
      
      # FuzeInfra service connections
      DATABASE_URL: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      REDIS_URL: redis://redis:6379
      MONGODB_URL: mongodb://${MONGO_INITDB_ROOT_USERNAME}:${MONGO_INITDB_ROOT_PASSWORD}@mongodb:27017
      
      # Optional services
      ELASTICSEARCH_URL: http://elasticsearch:9200
      NEO4J_URL: bolt://neo4j:7687
      RABBITMQ_URL: amqp://${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS}@rabbitmq:5672
      KAFKA_BROKERS: kafka:29092
      
      # Monitoring
      PROMETHEUS_URL: http://prometheus:9090
      
    networks:
      - FuzeInfra
    depends_on:
      - postgres
      - redis
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
    restart: unless-stopped

  # Frontend service (if applicable)
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: ${PROJECT_NAME:-myproject}-frontend
    ports:
      - "${FRONTEND_PORT:-3001}:3000"
    environment:
      REACT_APP_API_URL: http://${PROJECT_NAME:-myproject}.dev.local:${API_PORT:-3000}
      REACT_APP_WS_URL: ws://${PROJECT_NAME:-myproject}.dev.local:${API_PORT:-3000}
    networks:
      - FuzeInfra
    depends_on:
      - api
    restart: unless-stopped
```

### 2. **Environment Configuration**

Create a `.env` file in your project:

```bash
# Project Configuration
PROJECT_NAME=myproject
NODE_ENV=development
API_PORT=3000
FRONTEND_PORT=3001

# Copy these from FuzeInfra's .env file
POSTGRES_USER=fuzeinfra
POSTGRES_PASSWORD=fuzeinfra_secure_password
POSTGRES_DB=fuzeinfra_db
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=mongodb_secure_password
RABBITMQ_DEFAULT_USER=admin
RABBITMQ_DEFAULT_PASS=admin123

# Optional: Override for project-specific databases
# PROJECT_POSTGRES_DB=myproject_db
# PROJECT_MONGO_DB=myproject
```

### 3. **DNS and HTTPS Setup**

Register your project with FuzeInfra's DNS and certificate systems:

```bash
# Navigate to FuzeInfra directory
cd path/to/FuzeInfra

# Add DNS entry for your project
python tools/dns-manager/dns-manager.py add myproject

# Generate certificates (if not done already)
./tools/cert-manager/setup-local-certs.sh

# Register for webhook management (optional)
python tools/tunnel-manager/tunnel-manager.py register myproject webhook --type generic
```

Your application will be available at:
- HTTP: `http://myproject.dev.local:3000`
- HTTPS: `https://myproject.dev.local` (via nginx proxy)
- Webhook endpoint: `https://myproject.webhook.your-domain.com`

## Service Connection Examples

### Node.js/TypeScript Applications

**package.json dependencies:**
```json
{
  "dependencies": {
    "pg": "^8.11.0",
    "redis": "^4.6.0",
    "mongodb": "^6.0.0",
    "elasticsearch": "^16.7.3",
    "neo4j-driver": "^5.12.0",
    "amqplib": "^0.10.3",
    "kafkajs": "^2.2.4"
  },
  "devDependencies": {
    "@types/pg": "^8.10.0"
  }
}
```

**Database connections:**
```typescript
// src/config/database.ts
import { Pool } from 'pg';
import { createClient } from 'redis';
import { MongoClient } from 'mongodb';
import { Client } from '@elastic/elasticsearch';
import neo4j from 'neo4j-driver';

// PostgreSQL
export const pgPool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: 30000,
});

// Redis
export const redisClient = createClient({
  url: process.env.REDIS_URL,
});
redisClient.on('error', (err) => console.error('Redis Client Error', err));
redisClient.connect();

// MongoDB
export const mongoClient = new MongoClient(process.env.MONGODB_URL!);
export const mongodb = mongoClient.db(process.env.PROJECT_MONGO_DB || 'myproject');

// Elasticsearch
export const esClient = new Client({
  node: process.env.ELASTICSEARCH_URL,
});

// Neo4j
export const neo4jDriver = neo4j.driver(
  process.env.NEO4J_URL!,
  neo4j.auth.basic('neo4j', 'password')
);
```

**Express.js health check:**
```typescript
// src/routes/health.ts
import express from 'express';
import { pgPool, redisClient, mongoClient } from '../config/database';

const router = express.Router();

router.get('/health', async (req, res) => {
  try {
    // Check database connections
    await pgPool.query('SELECT 1');
    await redisClient.ping();
    await mongoClient.db().admin().ping();
    
    res.json({
      status: 'healthy',
      timestamp: new Date().toISOString(),
      services: {
        postgres: 'connected',
        redis: 'connected',
        mongodb: 'connected'
      }
    });
  } catch (error) {
    res.status(503).json({
      status: 'unhealthy',
      error: error.message
    });
  }
});

export default router;
```

### Python/FastAPI Applications

**requirements.txt:**
```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
psycopg2-binary>=2.9.7
redis>=5.0.0
pymongo>=4.5.0
elasticsearch>=8.10.0
neo4j>=5.12.0
pika>=1.3.2
kafka-python>=2.0.2
```

**Database connections:**
```python
# app/config/database.py
import os
import psycopg2
from psycopg2.pool import SimpleConnectionPool
import redis
from pymongo import MongoClient
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase

# PostgreSQL connection pool
pg_pool = SimpleConnectionPool(
    1, 20,
    os.getenv('DATABASE_URL')
)

# Redis client
redis_client = redis.from_url(os.getenv('REDIS_URL'))

# MongoDB client
mongo_client = MongoClient(os.getenv('MONGODB_URL'))
mongodb = mongo_client[os.getenv('PROJECT_MONGO_DB', 'myproject')]

# Elasticsearch client
es_client = Elasticsearch([os.getenv('ELASTICSEARCH_URL')])

# Neo4j driver
neo4j_driver = GraphDatabase.driver(
    os.getenv('NEO4J_URL'),
    auth=('neo4j', 'password')
)
```

**FastAPI health check:**
```python
# app/routes/health.py
from fastapi import APIRouter, HTTPException
from app.config.database import pg_pool, redis_client, mongo_client

router = APIRouter()

@router.get("/health")
async def health_check():
    try:
        # Check PostgreSQL
        conn = pg_pool.getconn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        pg_pool.putconn(conn)
        
        # Check Redis
        redis_client.ping()
        
        # Check MongoDB
        mongo_client.admin.command('ping')
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "services": {
                "postgres": "connected",
                "redis": "connected", 
                "mongodb": "connected"
            }
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Health check failed: {str(e)}")
```

### React/Frontend Applications

**Environment variables (.env.local):**
```bash
REACT_APP_API_URL=http://myproject.dev.local:3000
REACT_APP_WS_URL=ws://myproject.dev.local:3000
REACT_APP_ENVIRONMENT=development
```

**API configuration:**
```typescript
// src/config/api.ts
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:3000';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor for auth tokens
apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token');
      window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);
```

## Monitoring Integration

### Prometheus Metrics

**Node.js with prom-client:**
```typescript
// src/metrics.ts
import client from 'prom-client';

// Create a Registry
const register = new client.Registry();

// Add default metrics
client.collectDefaultMetrics({ register });

// Custom metrics
export const httpRequestDuration = new client.Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request duration in seconds',
  labelNames: ['method', 'route', 'status'],
  registers: [register],
});

export const activeConnections = new client.Gauge({
  name: 'active_connections',
  help: 'Number of active connections',
  registers: [register],
});

// Metrics endpoint
export const metricsHandler = async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
};
```

**Python with prometheus_client:**
```python
# app/metrics.py
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST

# Define metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration')
ACTIVE_CONNECTIONS = Gauge('active_connections', 'Active connections')

@router.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

### Structured Logging

**Node.js with Winston:**
```typescript
// src/config/logger.ts
import winston from 'winston';

export const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.json()
  ),
  defaultMeta: { service: process.env.PROJECT_NAME || 'myproject' },
  transports: [
    new winston.transports.Console(),
    // Loki will collect logs from stdout
  ],
});
```

## Webhook Integration

### GitHub Webhooks

```typescript
// src/routes/webhooks.ts
import express from 'express';
import crypto from 'crypto';

const router = express.Router();

// Verify GitHub webhook signature
const verifyGitHubSignature = (payload: string, signature: string) => {
  const secret = process.env.GITHUB_WEBHOOK_SECRET;
  const expectedSignature = `sha256=${crypto
    .createHmac('sha256', secret)
    .update(payload)
    .digest('hex')}`;
  return crypto.timingSafeEqual(
    Buffer.from(signature),
    Buffer.from(expectedSignature)
  );
};

router.post('/github', express.raw({ type: 'application/json' }), (req, res) => {
  const signature = req.headers['x-hub-signature-256'] as string;
  const payload = req.body.toString();
  
  if (!verifyGitHubSignature(payload, signature)) {
    return res.status(401).json({ error: 'Invalid signature' });
  }
  
  const event = JSON.parse(payload);
  
  // Handle different GitHub events
  switch (req.headers['x-github-event']) {
    case 'push':
      handlePushEvent(event);
      break;
    case 'pull_request':
      handlePullRequestEvent(event);
      break;
    default:
      console.log('Unhandled GitHub event:', req.headers['x-github-event']);
  }
  
  res.json({ received: true });
});

export default router;
```

## Testing

### Integration Tests

**Jest configuration (jest.config.js):**
```javascript
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  setupFilesAfterEnv: ['<rootDir>/tests/setup.ts'],
  testMatch: ['**/__tests__/**/*.test.ts', '**/?(*.)+(spec|test).ts'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
  ],
};
```

**Test setup:**
```typescript
// tests/setup.ts
import { pgPool, redisClient, mongoClient } from '../src/config/database';

beforeAll(async () => {
  // Ensure test databases are clean
  await pgPool.query('BEGIN; TRUNCATE TABLE users CASCADE; COMMIT;');
  await redisClient.flushDb();
  await mongoClient.db('test').dropDatabase();
});

afterAll(async () => {
  await pgPool.end();
  await redisClient.quit();
  await mongoClient.close();
});
```

**Integration test example:**
```typescript
// tests/integration/api.test.ts
import request from 'supertest';
import { app } from '../src/app';
import { pgPool } from '../src/config/database';

describe('API Integration Tests', () => {
  test('POST /users creates a user', async () => {
    const userData = {
      email: 'test@example.com',
      name: 'Test User',
    };
    
    const response = await request(app)
      .post('/users')
      .send(userData)
      .expect(201);
    
    expect(response.body).toMatchObject(userData);
    
    // Verify in database
    const result = await pgPool.query('SELECT * FROM users WHERE email = $1', [userData.email]);
    expect(result.rows).toHaveLength(1);
  });
  
  test('GET /health returns healthy status', async () => {
    const response = await request(app)
      .get('/health')
      .expect(200);
    
    expect(response.body.status).toBe('healthy');
    expect(response.body.services).toEqual({
      postgres: 'connected',
      redis: 'connected',
      mongodb: 'connected',
    });
  });
});
```

## Deployment and Production Considerations

### Environment-Specific Configurations

Create different environment files:

```bash
# .env.development (local with FuzeInfra)
DATABASE_URL=postgresql://fuzeinfra:password@postgres:5432/fuzeinfra_db
REDIS_URL=redis://redis:6379

# .env.staging (external services)
DATABASE_URL=postgresql://user:pass@staging-db.company.com:5432/myproject_staging
REDIS_URL=redis://staging-redis.company.com:6379

# .env.production (external services)
DATABASE_URL=postgresql://user:pass@prod-db.company.com:5432/myproject_prod
REDIS_URL=redis://prod-redis.company.com:6379
```

### Docker Multi-stage Build

```dockerfile
# Dockerfile
FROM node:18-alpine AS base
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

FROM node:18-alpine AS development
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]

FROM base AS production
COPY . .
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
```

### Health Checks and Monitoring

Implement comprehensive health checks:

```typescript
// src/health/checks.ts
export interface HealthCheck {
  name: string;
  status: 'healthy' | 'unhealthy';
  responseTime?: number;
  error?: string;
}

export const checkDatabase = async (): Promise<HealthCheck> => {
  const start = Date.now();
  try {
    await pgPool.query('SELECT 1');
    return {
      name: 'database',
      status: 'healthy',
      responseTime: Date.now() - start,
    };
  } catch (error) {
    return {
      name: 'database',
      status: 'unhealthy',
      responseTime: Date.now() - start,
      error: error.message,
    };
  }
};
```

## Troubleshooting

### Common Issues

1. **Network connectivity issues:**
```bash
# Check if FuzeInfra network exists
docker network ls | grep FuzeInfra

# Inspect network configuration
docker network inspect FuzeInfra

# Test connectivity from your container
docker exec your-container ping postgres
```

2. **DNS resolution problems:**
```bash
# Check DNS manager status
python path/to/FuzeInfra/tools/dns-manager/dns-manager.py status

# Manually add DNS entry
python path/to/FuzeInfra/tools/dns-manager/dns-manager.py add myproject

# Test DNS resolution
nslookup myproject.dev.local 127.0.0.1
```

3. **Certificate issues:**
```bash
# Regenerate certificates
cd path/to/FuzeInfra
./tools/cert-manager/setup-local-certs.sh

# Restart nginx
docker-compose -f docker-compose.FuzeInfra.yml restart nginx

# Test HTTPS
curl -k https://myproject.dev.local/health
```

4. **Database connection issues:**
```bash
# Check if services are running
docker ps | grep fuzeinfra

# Check service logs
docker logs fuzeinfra-postgres
docker logs fuzeinfra-redis
docker logs fuzeinfra-mongodb

# Test connections manually
docker exec -it fuzeinfra-postgres psql -U fuzeinfra -d fuzeinfra_db
docker exec -it fuzeinfra-redis redis-cli ping
docker exec -it fuzeinfra-mongodb mongosh --username admin
```

## Best Practices

1. **Resource Management:**
   - Use connection pooling for databases
   - Implement proper connection cleanup
   - Set appropriate timeouts and retry logic

2. **Security:**
   - Never hardcode credentials
   - Use environment variables for all configuration
   - Implement proper input validation
   - Use HTTPS in production

3. **Monitoring:**
   - Implement comprehensive health checks
   - Export Prometheus metrics
   - Use structured logging
   - Monitor resource usage

4. **Development:**
   - Use consistent naming conventions
   - Implement proper error handling
   - Write comprehensive tests
   - Document API endpoints

5. **Deployment:**
   - Use multi-stage Docker builds
   - Implement graceful shutdown
   - Handle signal termination properly
   - Use proper container resource limits

This guide provides a solid foundation for integrating your applications with FuzeInfra. For specific use cases or advanced configurations, refer to the main CLAUDE.md file or reach out to the FuzeInfra maintainers.