# Local Development Orchestrator - Implementation Plan

## 🎯 **Project Overview**

A comprehensive system to eliminate port conflicts and provide clean URLs for multiple Docker projects running in parallel. The system provides automatic port allocation, nginx configuration management, DNS routing, and seamless project deployment.

## 📋 **System Architecture**

```
Developer Command: ./deploy-project.sh sportsbuck
    ↓
1. Port Allocator → Discovers available ports
2. EnvManager → Injects ports into .env file
3. Nginx Config Generator → Creates project-specific nginx config
4. Config Deployer → Deploys config to shared nginx
5. Nginx Reloader → Hot reloads nginx configuration
6. DNS Manager → Updates local DNS routing
7. Docker Orchestrator → Builds and starts containers
    ↓
Result: sportsbuck.dev.local accessible on port 80
```

## 🏗️ **Components to Implement**

### **1. Port Allocator Service**
- **Purpose**: Real-time port discovery and allocation
- **Location**: `tools/port-allocator/`
- **Technology**: Python/Node.js/Go
- **Key Features**:
  - Scan available ports in configurable ranges
  - Allocate consecutive port blocks per project
  - No persistent database (real-time scanning)

### **2. Environment Manager Integration**
- **Purpose**: Inject allocated ports into project .env files
- **Location**: `tools/env-manager/`
- **Integration**: Extends existing EnvManager
- **Key Features**:
  - Template-based .env generation
  - Port variable injection
  - Project-specific configurations

### **3. Nginx Configuration Generator**
- **Purpose**: Generate project-specific nginx configs
- **Location**: `tools/nginx-generator/`
- **Technology**: Template engine (Jinja2/Handlebars)
- **Key Features**:
  - Dynamic upstream generation
  - SSL support preparation
  - Load balancing configurations

### **4. Shared Nginx Infrastructure**
- **Purpose**: Central nginx instance for all projects
- **Location**: `infrastructure/shared-nginx/`
- **Technology**: Docker container with volume mounts
- **Key Features**:
  - Hot-reload capability
  - Configuration directory mounting
  - Health check endpoints

### **5. DNS Management Service**
- **Purpose**: Local DNS routing for *.dev.local domains
- **Location**: `tools/dns-manager/`
- **Technology**: Platform-specific (hosts file/dnsmasq)
- **Key Features**:
  - Automatic hosts file updates
  - Cross-platform support
  - Cleanup on project removal

### **6. Project Orchestrator**
- **Purpose**: Main deployment script coordinating all components
- **Location**: `tools/orchestrator/`
- **Technology**: Shell script with Python/Node.js helpers
- **Key Features**:
  - Single command deployment
  - Error handling and rollback
  - Status reporting

## 📁 **File Structure**

```
FuzeInfra/
├── tools/
│   ├── port-allocator/
│   │   ├── port-allocator.py
│   │   ├── config.yaml
│   │   └── requirements.txt
│   ├── env-manager/
│   │   ├── env-injector.py
│   │   ├── templates/
│   │   │   └── project.env.template
│   │   └── config.yaml
│   ├── nginx-generator/
│   │   ├── nginx-generator.py
│   │   ├── templates/
│   │   │   ├── basic-proxy.conf.template
│   │   │   ├── api-gateway.conf.template
│   │   │   └── spa-app.conf.template
│   │   └── config.yaml
│   ├── dns-manager/
│   │   ├── dns-manager.py
│   │   ├── platform/
│   │   │   ├── windows.py
│   │   │   ├── linux.py
│   │   │   └── macos.py
│   │   └── config.yaml
│   └── orchestrator/
│       ├── deploy-project.sh
│       ├── remove-project.sh
│       ├── list-projects.sh
│       └── orchestrator.py
├── infrastructure/
│   └── shared-nginx/
│       ├── docker-compose.yml
│       ├── nginx.conf
│       ├── conf.d/
│       │   └── default.conf
│       └── scripts/
│           ├── start-nginx.sh
│           └── reload-nginx.sh
└── projects/
    ├── sportsbuck/
    │   ├── .dev-config.yaml
    │   └── docker-compose.yml
    └── other-project/
        ├── .dev-config.yaml
        └── docker-compose.yml
```

## 🔧 **Implementation Details**

### **1. Port Allocator (`tools/port-allocator/port-allocator.py`)**

```python
# Key Functions:
def scan_available_ports(start_port=3000, end_port=9999):
    """Scan for available ports in range"""
    
def allocate_port_block(project_name, num_ports=5):
    """Allocate consecutive ports for a project"""
    
def get_project_ports(project_name):
    """Get currently allocated ports for a project"""
    
def validate_port_availability(ports):
    """Validate that ports are still available"""

# Configuration:
config.yaml:
  port_ranges:
    - start: 3000
      end: 3999
      description: "Frontend applications"
    - start: 5000  
      end: 5999
      description: "Backend APIs"
    - start: 6379
      end: 6399
      description: "Redis instances"
```

### **2. Environment Manager (`tools/env-manager/env-injector.py`)**

```python
# Key Functions:
def inject_ports_to_env(project_path, port_allocation):
    """Inject allocated ports into project .env file"""
    
def generate_env_from_template(template_path, variables):
    """Generate .env from template with variables"""
    
def backup_existing_env(project_path):
    """Backup existing .env before modification"""

# Template Example (.env.template):
# Frontend
FRONTEND_PORT=${FRONTEND_PORT}
FRONTEND_HOST=0.0.0.0

# Backend  
BACKEND_PORT=${BACKEND_PORT}
BACKEND_HOST=0.0.0.0

# Database
MONGODB_PORT=${MONGODB_PORT}
REDIS_PORT=${REDIS_PORT}
```

### **3. Nginx Generator (`tools/nginx-generator/nginx-generator.py`)**

```python
# Key Functions:
def generate_nginx_config(project_config, port_allocation):
    """Generate nginx config from project specs and ports"""
    
def select_template(project_type):
    """Select appropriate nginx template based on project type"""
    
def deploy_config_to_shared_nginx(config_content, project_name):
    """Deploy generated config to shared nginx instance"""

# Template Example (basic-proxy.conf.template):
server {
    listen 80;
    server_name ${PROJECT_NAME}.dev.local;
    
    location / {
        proxy_pass http://localhost:${FRONTEND_PORT};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /api/ {
        proxy_pass http://localhost:${BACKEND_PORT};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### **4. Shared Nginx Infrastructure (`infrastructure/shared-nginx/`)**

```yaml
# docker-compose.yml
version: '3.8'
services:
  shared-nginx:
    image: nginx:alpine
    container_name: fuzeinfra-shared-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./conf.d:/etc/nginx/conf.d:rw
      - nginx_logs:/var/log/nginx
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "nginx", "-t"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  nginx_logs:
```

### **5. DNS Manager (`tools/dns-manager/dns-manager.py`)**

```python
# Key Functions:
def add_dns_entry(project_name, domain_suffix="dev.local"):
    """Add DNS entry for project"""
    
def remove_dns_entry(project_name, domain_suffix="dev.local"):
    """Remove DNS entry for project"""
    
def platform_specific_dns_update(domain, ip="127.0.0.1"):
    """Update DNS based on operating system"""

# Platform Implementations:
# Windows: Update C:\Windows\System32\drivers\etc\hosts
# Linux/Mac: Update /etc/hosts
# Advanced: Setup dnsmasq for *.dev.local → 127.0.0.1
```

### **6. Project Configuration (`.dev-config.yaml`)**

```yaml
# Example for SportsBuck
project:
  name: sportsbuck
  type: fullstack-app
  domain: sportsbuck.dev.local
  
services:
  frontend:
    port_var: FRONTEND_PORT
    nginx_location: /
    health_check: /
    
  backend:
    port_var: BACKEND_PORT  
    nginx_location: /api/
    health_check: /health
    
  mongodb:
    port_var: MONGODB_PORT
    nginx_expose: false
    
  redis:
    port_var: REDIS_PORT
    nginx_expose: false

nginx:
  template: fullstack-spa
  ssl: false
  custom_locations:
    - location: /metrics
      proxy_pass: backend
      auth_required: true
```

## 🚀 **Main Orchestrator Script**

### **`tools/orchestrator/deploy-project.sh`**

```bash
#!/bin/bash

# Usage: ./deploy-project.sh <project-name> [project-path]

PROJECT_NAME=$1
PROJECT_PATH=${2:-"../projects/$PROJECT_NAME"}

echo "🚀 Deploying $PROJECT_NAME..."

# 1. Validate project configuration
python3 validate-project.py "$PROJECT_PATH"

# 2. Allocate ports
echo "📊 Allocating ports..."
PORTS=$(python3 ../port-allocator/port-allocator.py allocate "$PROJECT_NAME" 5)

# 3. Update environment variables  
echo "⚙️ Updating environment variables..."
python3 ../env-manager/env-injector.py "$PROJECT_PATH" "$PORTS"

# 4. Generate nginx configuration
echo "🌐 Generating nginx configuration..."
python3 ../nginx-generator/nginx-generator.py "$PROJECT_PATH" "$PORTS"

# 5. Update DNS routing
echo "🔗 Updating DNS routing..."
python3 ../dns-manager/dns-manager.py add "$PROJECT_NAME"

# 6. Reload nginx
echo "🔄 Reloading nginx..."
docker exec fuzeinfra-shared-nginx nginx -s reload

# 7. Start project containers
echo "🐳 Starting containers..."
cd "$PROJECT_PATH"
docker-compose up -d

# 8. Health check and status report
echo "✅ Deployment complete!"
echo "🌐 Access your app at: http://$PROJECT_NAME.dev.local"
echo "📊 Allocated ports: $PORTS"
```

## 🧪 **Testing Strategy**

### **Unit Tests**
- Port allocation logic
- Environment variable injection
- Nginx configuration generation
- DNS management functions

### **Integration Tests**
- End-to-end project deployment
- Multiple project scenarios
- Port conflict resolution
- Nginx reload verification

### **Manual Testing Scenarios**
1. Deploy single project
2. Deploy multiple projects simultaneously
3. Stop/start projects (port reallocation)
4. System restart recovery
5. Error handling (port exhaustion, nginx errors)

## 📋 **Implementation Phases**

### **Phase 1: Core Components (Week 1)**
- [ ] Port Allocator implementation
- [ ] Basic Environment Manager integration
- [ ] Simple Nginx configuration generator
- [ ] Basic DNS management (hosts file)

### **Phase 2: Integration (Week 2)**
- [ ] Shared Nginx infrastructure setup
- [ ] Main orchestrator script
- [ ] Project configuration validation
- [ ] Error handling and logging

### **Phase 3: Enhancement (Week 3)**
- [ ] Advanced nginx templates
- [ ] SSL support preparation
- [ ] Platform-specific DNS improvements
- [ ] Monitoring and health checks

### **Phase 4: Testing & Documentation (Week 4)**
- [ ] Comprehensive testing suite
- [ ] User documentation
- [ ] Troubleshooting guides
- [ ] Performance optimization

## 🔧 **Configuration Files**

### **Global Configuration (`tools/config/global.yaml`)**

```yaml
system:
  domain_suffix: dev.local
  shared_nginx_container: fuzeinfra-shared-nginx
  
port_allocation:
  ranges:
    frontend: [3000, 3999]
    backend: [5000, 5999] 
    database: [6000, 6999]
    cache: [7000, 7999]
  consecutive_block_size: 5
  
nginx:
  config_path: /etc/nginx/conf.d
  reload_command: nginx -s reload
  template_dir: templates/
  
dns:
  strategy: hosts_file  # hosts_file | dnsmasq | systemd_resolved
  backup_enabled: true
```

## 🚨 **Error Handling & Recovery**

### **Common Error Scenarios**
1. **Port Exhaustion**: Graceful degradation, larger port ranges
2. **Nginx Reload Failure**: Config validation, rollback mechanism  
3. **DNS Update Failure**: Permission issues, fallback strategies
4. **Container Start Failure**: Port release, cleanup procedures

### **Recovery Mechanisms**
- Configuration backups before changes
- Atomic operations where possible
- Cleanup scripts for failed deployments
- Health check validation

## 📊 **Monitoring & Observability**

### **Metrics to Track**
- Port allocation efficiency
- Nginx reload success rate
- Project deployment time
- Resource utilization per project

### **Logging Strategy**
- Centralized logging for all components
- Structured logs (JSON format)
- Log rotation and retention
- Debug mode for troubleshooting

## 🎯 **Success Criteria**

### **Developer Experience**
- Single command project deployment
- Clean, memorable URLs
- Zero port conflicts
- Fast deployment (< 30 seconds)

### **System Reliability** 
- 99%+ nginx reload success rate
- Automatic cleanup of failed deployments
- Graceful handling of system restarts
- No manual intervention required

### **Scalability**
- Support 10+ concurrent projects
- Efficient port utilization
- Minimal resource overhead
- Easy addition of new project types

## 🔄 **Future Enhancements**

### **Advanced Features**
- SSL certificate automation (Let's Encrypt)
- Load balancing for multiple instances
- Service mesh integration
- Container orchestration (K8s support)

### **Developer Tools**
- IDE integration
- Project status dashboard
- Performance monitoring
- Automated testing pipeline

---

## 🚀 **Getting Started**

1. **Clone FuzeInfra repository**
2. **Create the directory structure** as outlined above
3. **Implement Phase 1 components** starting with port-allocator
4. **Test with a single project** (SportsBuck)
5. **Iterate and enhance** based on real usage

This implementation plan provides a comprehensive foundation for eliminating port management nightmares while providing a professional development experience with clean URLs and zero conflicts. 