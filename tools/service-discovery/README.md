# FuzeInfra Service Discovery System

Industry-standard service discovery using **Consul** for FuzeInfra platform. This system enables projects like `fuzereach` to discover and connect to services like `fuzeagent` without hardcoded URLs or direct container checking.

## Overview

The FuzeInfra Service Discovery system provides:

- **Service Registration**: Automatic registration of services with health checks
- **Service Discovery**: Dynamic discovery of healthy service instances
- **Health Monitoring**: Continuous health checking and status reporting
- **DNS Integration**: Service discovery via DNS queries (`service.dev.local`)
- **Load Balancing**: Multiple instance support with health-aware routing
- **Graceful Shutdown**: Automatic service deregistration on shutdown

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   fuzereach     │    │     Consul      │    │   fuzeagent     │
│                 │    │                 │    │                 │
│ 1. Register     │───▶│  Service        │◀───│ 1. Register     │
│ 2. Discover     │◀───│  Registry       │    │ 2. Health Check │
│ 3. Call API     │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       ▲
         │              ┌─────────────────┐              │
         └──────────────▶│  Direct API     │──────────────┘
                        │  Communication  │
                        └─────────────────┘
```

## Quick Start

### 1. Start FuzeInfra with Consul

```bash
cd /path/to/FuzeInfra
./infra-up.sh
```

This starts Consul along with other FuzeInfra services:
- **Consul UI**: http://localhost:8500
- **DNS Interface**: localhost:8600

### 2. Register a Service

```bash
cd tools/service-discovery
python service-discovery.py register fuzeagent --port 3000 --health-check /api/health --tags api,backend
```

### 3. Discover a Service

```bash
python service-discovery.py discover fuzeagent
```

Output:
```
🔍 Found 1 instance(s) for service 'fuzeagent':

  1. 🟢 fuzeagent-3000
     Address: fuzeagent:3000
     URL: http://fuzeagent:3000
     Domain: fuzeagent.dev.local
     Domain URL: http://fuzeagent.dev.local
     Health: passing (1 checks)
     Tags: api, backend
```

### 4. Use in Your Application

See [client-examples/](client-examples/) for integration patterns.

## CLI Reference

### Installation

```bash
cd tools/service-discovery
pip install -r requirements.txt
```

### Commands

#### Register Service
```bash
python service-discovery.py register <service-name> --port <port> [options]

Options:
  --address ADDR           Service address (defaults to service name)
  --health-check PATH      Health check endpoint (e.g., /health)
  --health-interval TIME   Health check interval (default: 10s)
  --health-timeout TIME    Health check timeout (default: 5s)
  --tags TAGS             Comma-separated tags
  --meta KEY=VALUE        Comma-separated metadata
  --json                  JSON output
```

Example:
```bash
python service-discovery.py register fuzeagent \
  --port 3000 \
  --health-check /api/health \
  --tags api,backend,v1 \
  --meta version=1.2.0,environment=development
```

#### Discover Service
```bash
python service-discovery.py discover <service-name> [options]

Options:
  --include-unhealthy     Include unhealthy instances
  --json                  JSON output
```

#### List All Services
```bash
python service-discovery.py list [--json]
```

#### Check Service Health
```bash
python service-discovery.py health <service-name> [--verbose] [--json]
```

#### Check Consul Status
```bash
python service-discovery.py status [--json]
```

#### Deregister Service
```bash
python service-discovery.py deregister <service-name> [--port PORT]
```

## Integration Examples

### fuzereach → fuzeagent Pattern

This is the exact use case you described - `fuzereach` needs to discover and call `fuzeagent` APIs.

#### In fuzeagent (Python/FastAPI):

```python
from fastapi import FastAPI
import sys
sys.path.append('../FuzeInfra/tools/service-discovery')
from consul_helper import ConsulHelper

app = FastAPI()
service_discovery = ConsulHelper()

@app.on_event("startup")
async def startup():
    # Register fuzeagent with Consul
    service_discovery.register_service(
        name="fuzeagent",
        port=3000,
        health_check_path="/api/health",
        tags=["api", "agent", "backend"]
    )

@app.get("/api/health")
async def health():
    return {"status": "healthy", "service": "fuzeagent"}

@app.post("/api/process")
async def process_data(data: dict):
    return {"processed": True, "result": data}
```

#### In fuzereach (Node.js):

```javascript
const FuzeInfraServiceDiscovery = require('../FuzeInfra/tools/service-discovery/client-examples/nodejs-example.js');

class FuzeReachService {
    constructor() {
        this.serviceDiscovery = new FuzeInfraServiceDiscovery();
    }

    async start() {
        // Register fuzereach
        await this.serviceDiscovery.registerService('fuzereach', 3001, {
            healthCheck: '/health',
            tags: ['web', 'frontend']
        });
    }

    async callFuzeAgent(data) {
        // Discover fuzeagent - NO hardcoded URLs!
        const fuzeagentUrl = await this.serviceDiscovery.getServiceUrl('fuzeagent');
        
        const response = await fetch(`${fuzeagentUrl}/api/process`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        return await response.json();
    }
}
```

#### Benefits of This Approach

1. **No Hardcoded URLs**: Services discover each other dynamically
2. **Health Awareness**: Only connects to healthy fuzeagent instances
3. **Automatic Failover**: If one fuzeagent instance fails, discovers others
4. **Environment Agnostic**: Works in Docker, local development, and production
5. **Clean Separation**: FuzeInfra provides discovery, applications focus on business logic

### Multiple Instances Support

```bash
# Register multiple fuzeagent instances
python service-discovery.py register fuzeagent --port 3000 --health-check /api/health
python service-discovery.py register fuzeagent --port 3001 --health-check /api/health
python service-discovery.py register fuzeagent --port 3002 --health-check /api/health

# fuzereach will automatically discover all healthy instances
```

## DNS Integration

Services are also discoverable via DNS:

```bash
# Query via Consul DNS interface
dig @localhost -p 8600 fuzeagent.service.consul

# Or use the domain pattern
curl http://fuzeagent.dev.local/api/health
```

## Health Monitoring

### Automatic Health Checks

When registering with `--health-check`, Consul automatically monitors service health:

```bash
python service-discovery.py register fuzeagent --port 3000 --health-check /api/health
```

Consul will:
- Check `http://fuzeagent:3000/api/health` every 10 seconds
- Mark service as unhealthy if check fails
- Remove unhealthy instances from discovery results

### Manual Health Checks

```bash
# Check specific service health
python service-discovery.py health fuzeagent

# Output shows detailed health information
🟢 Service 'fuzeagent' - HEALTHY
   Total Instances: 2
   Healthy: 2
   Unhealthy: 0

  1. 🟢 fuzeagent-3000 (fuzeagent:3000)
     ✅ Service 'fuzeagent' check: passing

  2. 🟢 fuzeagent-3001 (fuzeagent:3001)
     ✅ Service 'fuzeagent' check: passing
```

## Development Workflow

### 1. Start FuzeInfra
```bash
cd FuzeInfra
./infra-up.sh
```

### 2. Start Your Services
Each service registers itself on startup:

```bash
# Terminal 1: Start fuzeagent
cd fuzeagent
npm start  # or python main.py

# Terminal 2: Start fuzereach  
cd fuzereach
npm start
```

### 3. Verify Service Discovery
```bash
cd FuzeInfra/tools/service-discovery
python service-discovery.py list
```

### 4. Test Inter-Service Communication
```bash
# Test that fuzereach can call fuzeagent
curl http://fuzereach.dev.local/api/call-fuzeagent
```

## Production Considerations

### High Availability
- Run multiple Consul servers in production
- Use Consul Connect for service mesh features
- Implement circuit breakers for service calls

### Security
- Enable Consul ACLs for production
- Use TLS for Consul communication
- Implement service-to-service authentication

### Monitoring
- Monitor Consul cluster health
- Track service registration/deregistration events
- Alert on service health check failures

## Troubleshooting

### Common Issues

**Service not found:**
```bash
# Check if service is registered
python service-discovery.py list | grep fuzeagent

# Check service health
python service-discovery.py health fuzeagent
```

**Consul connection failed:**
```bash
# Check Consul status
python service-discovery.py status

# Check if Consul is running
docker ps | grep consul
```

**Health check failures:**
```bash
# Check health endpoint directly
curl http://fuzeagent:3000/api/health

# Check Consul logs
docker logs fuzeinfra-consul
```

### Debug Mode
```bash
# Enable verbose output
python service-discovery.py health fuzeagent --verbose --json
```

## Files Structure

```
tools/service-discovery/
├── consul-helper.py          # Core Consul wrapper class
├── service-discovery.py      # CLI tool
├── config.yaml              # Configuration
├── requirements.txt         # Python dependencies
├── client-examples/         # Integration examples
│   ├── nodejs-example.js    # Node.js client library
│   ├── python-example.py    # Python client library
│   ├── package.json         # Node.js dependencies
│   ├── requirements.txt     # Python client dependencies
│   └── README.md           # Client usage examples
└── README.md               # This file
```

## Next Steps

1. **Start using service discovery** in your applications
2. **Integrate with nginx-generator** for automatic reverse proxy configuration
3. **Add monitoring dashboards** in Grafana for service discovery metrics
4. **Implement Consul Connect** for service mesh features in production

This system provides the industry-standard service discovery solution you requested, eliminating the need for direct container checking while maintaining clean separation between infrastructure and application concerns.