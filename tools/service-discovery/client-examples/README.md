# FuzeInfra Service Discovery Client Examples

This directory contains client library examples for integrating with FuzeInfra's Consul-based service discovery system.

## Quick Start

### Node.js Example

1. Install dependencies:
```bash
npm install
```

2. Run the example:
```bash
node nodejs-example.js
```

3. Use in your project:
```javascript
const FuzeInfraServiceDiscovery = require('./path/to/nodejs-example.js');

const serviceDiscovery = new FuzeInfraServiceDiscovery();

// Register your service
await serviceDiscovery.registerService('my-service', 3000, {
  healthCheck: '/health',
  tags: ['api', 'v1']
});

// Discover other services
const fuzeagentUrl = await serviceDiscovery.getServiceUrl('fuzeagent');
```

### Python Example

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the example:
```bash
python python-example.py
```

3. Use in your FastAPI project:
```python
from python_example import FuzeInfraServiceDiscovery

service_discovery = FuzeInfraServiceDiscovery()

# Register your service
service_discovery.register_service(
    service_name='my-service',
    port=8000,
    health_check_path='/health',
    tags=['api', 'python']
)

# Discover other services
fuzeagent_url = service_discovery.get_service_url('fuzeagent')
```

## Integration Patterns

### fuzereach → fuzeagent Example

**In fuzereach (Node.js):**
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

        // Setup graceful shutdown
        this.serviceDiscovery.setupGracefulShutdown('fuzereach', 3001);
    }

    async callFuzeAgent(data) {
        try {
            // Discover fuzeagent
            const fuzeagentUrl = await this.serviceDiscovery.getServiceUrl('fuzeagent');
            
            // Make API call
            const response = await fetch(`${fuzeagentUrl}/api/process`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            return await response.json();
        } catch (error) {
            console.error('Failed to call fuzeagent:', error.message);
            throw error;
        }
    }
}
```

**In fuzeagent (Python):**
```python
from fastapi import FastAPI
from python_example import FuzeInfraServiceDiscovery

app = FastAPI()
service_discovery = FuzeInfraServiceDiscovery()

@app.on_event("startup")
async def startup():
    # Register fuzeagent
    service_discovery.register_service(
        service_name='fuzeagent',
        port=3000,
        health_check_path='/health',
        tags=['api', 'agent', 'backend']
    )
    service_discovery.setup_graceful_shutdown('fuzeagent', 3000)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/api/process")
async def process_data(data: dict):
    # Process the data from fuzereach
    return {"processed": True, "data": data}
```

## Features

### Service Registration
- Automatic health check configuration
- Service metadata and tagging
- Graceful shutdown handling
- Docker network integration

### Service Discovery
- Health-aware service lookup
- Multiple instance handling
- Domain-based and direct URL access
- Real-time service status

### Integration Benefits
- **No hardcoded URLs**: Services discover each other dynamically
- **Health awareness**: Only connect to healthy service instances
- **Load balancing**: Automatically use available instances
- **Development friendly**: Works with both Docker containers and local development

## Advanced Usage

### Custom Health Checks
```javascript
// Custom health check with additional metadata
await serviceDiscovery.registerService('my-service', 3000, {
    healthCheck: '/api/health',
    healthInterval: '5s',
    healthTimeout: '3s',
    tags: ['api', 'v2', 'critical'],
    meta: {
        version: '2.1.0',
        environment: 'development',
        capabilities: 'auth,data'
    }
});
```

### Service Discovery with Fallback
```python
def get_service_with_fallback(service_name, fallback_url=None):
    try:
        return service_discovery.get_service_url(service_name)
    except Exception:
        if fallback_url:
            print(f"Using fallback URL for {service_name}: {fallback_url}")
            return fallback_url
        raise Exception(f"Service {service_name} unavailable and no fallback provided")
```

### Multi-Instance Load Balancing
```javascript
// Get all healthy instances and implement custom load balancing
const discovery = await serviceDiscovery.discoverService('my-service');
if (discovery.success && discovery.instances.length > 0) {
    // Round-robin, random, or custom selection logic
    const instance = discovery.instances[Math.floor(Math.random() * discovery.instances.length)];
    const serviceUrl = instance.url;
}
```

## Troubleshooting

### Common Issues

1. **Service not found**: Ensure the service is registered and healthy
2. **Connection refused**: Check if Consul is running on localhost:8500
3. **Health check failures**: Verify health check endpoint is accessible
4. **Docker networking**: Use container names as addresses in Docker environment

### Debug Commands
```bash
# Check service registration
python ../service-discovery.py list

# Check service health
python ../service-discovery.py health fuzeagent

# Check Consul status
python ../service-discovery.py status
```