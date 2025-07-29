# FuzeInfra Port Conflict Resolution

This document outlines the port conflict resolution strategy for deploying FuzeInfra alongside existing services on shared infrastructure.

## 🎯 Overview

FuzeInfra takes **precedence** over existing services when port conflicts occur. This ensures consistent service discovery and networking for applications that depend on FuzeInfra.

## 📋 FuzeInfra Reserved Ports

### Critical Infrastructure Ports (Priority 1-10)
These ports are **never** remapped and always take precedence:

| Port | Service | Protocol | Purpose | Container Name |
|------|---------|----------|---------|----------------|
| 53 | dnsmasq | UDP | DNS Resolution | fuzeinfra-dnsmasq |
| 80 | nginx | TCP | HTTP Proxy | fuzeinfra-nginx |
| 443 | nginx | TCP | HTTPS Proxy | fuzeinfra-nginx |

### Database Ports (Priority 11-20)
Database services have high priority due to application dependencies:

| Port | Service | Protocol | Purpose | Container Name |
|------|---------|----------|---------|----------------|
| 5432 | PostgreSQL | TCP | Primary Database | fuzeinfra-postgres |
| 27017 | MongoDB | TCP | Document Database | fuzeinfra-mongodb |
| 6379 | Redis | TCP | Cache/Session Store | fuzeinfra-redis |

### Application Ports (Priority 21-30)
Core application services:

| Port | Service | Protocol | Purpose | Container Name |
|------|---------|----------|---------|----------------|
| 8003 | ChromaDB | TCP | Vector Database | fuzeinfra-chromadb |
| 7474 | Neo4j | TCP | Graph Database | fuzeinfra-neo4j |
| 9092 | Kafka | TCP | Message Streaming | fuzeinfra-kafka |
| 5672 | RabbitMQ | TCP | Message Broker | fuzeinfra-rabbitmq |

### Monitoring Ports (Priority 31-40)
Monitoring and observability services:

| Port | Service | Protocol | Purpose | Container Name |
|------|---------|----------|---------|----------------|
| 3001 | Grafana | TCP | Dashboards | fuzeinfra-grafana |
| 9090 | Prometheus | TCP | Metrics Collection | fuzeinfra-prometheus |
| 8082 | Airflow | TCP | Workflow Orchestration | fuzeinfra-airflow |

### Management UI Ports (Priority 41-50)
Web interfaces for service management:

| Port | Service | Protocol | Purpose | Container Name |
|------|---------|----------|---------|----------------|
| 8081 | Mongo Express | TCP | MongoDB UI | fuzeinfra-mongo-express |
| 8080 | Kafka UI | TCP | Kafka Management | fuzeinfra-kafka-ui |
| 15672 | RabbitMQ Management | TCP | RabbitMQ UI | fuzeinfra-rabbitmq |
| 5555 | Flower | TCP | Celery Monitor | fuzeinfra-flower |
| 8053 | DNSMasq UI | TCP | DNS Management | fuzeinfra-dnsmasq |

## 🔧 Conflict Resolution Process

### Automatic Detection
The deployment system automatically detects port conflicts by:

1. **Port Scanning**: Checking if FuzeInfra ports are in use
2. **Process Identification**: Determining which services are using conflicted ports
3. **Service Mapping**: Identifying Docker containers and system services
4. **Priority Assessment**: Determining if services can be safely remapped

### Resolution Strategy

#### 1. FuzeInfra Takes Precedence
When FuzeInfra services conflict with existing services:

```bash
# Example: Existing nginx on port 80
# FuzeInfra nginx will take port 80
# Existing nginx will be remapped to port 8080
```

#### 2. Graceful Service Migration
For existing Docker services:

```bash
# Stop the existing service
docker stop existing-nginx

# Update the service configuration to use new port
# Restart with new port mapping
docker run -d --name existing-nginx -p 8080:80 nginx
```

#### 3. System Service Handling
For system services (non-Docker):

```bash
# Example: Apache running on port 80
sudo systemctl stop apache2
sudo sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf
sudo systemctl start apache2
```

### Implementation in Terraform

The Terraform configuration includes port remapping rules:

```hcl
variable "port_remapping_rules" {
  description = "Rules for existing services that need port remapping"
  type = map(object({
    service_name = string
    old_port     = number
    new_port     = number
    protocol     = string
    priority     = number # Lower number = higher priority
  }))
  default = {
    "existing-nginx" = {
      service_name = "existing-nginx"
      old_port     = 80
      new_port     = 8080
      protocol     = "tcp"
      priority     = 100  # Lower priority than FuzeInfra
    }
    "existing-postgres" = {
      service_name = "existing-postgres"
      old_port     = 5432
      new_port     = 5433
      protocol     = "tcp"
      priority     = 100
    }
  }
}
```

## 🚀 Deployment Scenarios

### Scenario 1: Fresh Installation
- **No conflicts**: All FuzeInfra services deploy on standard ports
- **Network creation**: `FuzeInfra` Docker network is created
- **DNS setup**: dnsmasq configures `*.dev.local` wildcard resolution

### Scenario 2: Existing Docker Services
- **Detection**: Deployment script identifies conflicting containers
- **Graceful shutdown**: Existing services are stopped
- **Port remapping**: Services restart on alternative ports
- **Documentation**: Changes are logged in deployment report

```bash
# Example conflict resolution log:
# Detected conflict: existing-nginx on port 80
# Stopping existing-nginx container
# Updating existing-nginx port mapping: 80 -> 8080
# Starting FuzeInfra nginx on port 80
# Restarting existing-nginx on port 8080
```

### Scenario 3: System Services
- **Service detection**: Non-Docker services identified via `netstat`/`lsof`
- **Configuration update**: Service configs modified for new ports
- **Service restart**: System services restarted with new configuration
- **Health verification**: Both services verified as healthy

### Scenario 4: Force Recreation Mode
When `FORCE_RECREATE=true`:

- **Complete cleanup**: All existing containers on FuzeInfra ports are stopped
- **Volume preservation**: Data volumes are preserved unless explicitly removed
- **Fresh deployment**: All FuzeInfra services deploy cleanly
- **Service restoration**: Other services can be restarted manually on alternative ports

## ⚙️ Configuration Management

### Environment Variables
Control conflict resolution behavior:

```bash
# Deployment configuration
FORCE_RECREATE=false              # Gentle migration vs. force recreation
BACKUP_BEFORE_DEPLOY=true         # Always backup before changes
HEALTH_CHECK_TIMEOUT=300          # Time to wait for service health
PORT_CONFLICT_STRATEGY=remap      # remap|force|abort

# Port remapping preferences
DEFAULT_REMAP_OFFSET=1000         # Add 1000 to conflicted ports
PRESERVE_SYSTEM_SERVICES=true     # Don't modify system services
NOTIFY_PORT_CHANGES=true          # Send notifications about port changes
```

### Service Priority Matrix

| Priority Range | Service Type | Remapping Policy |
|----------------|--------------|------------------|
| 1-10 | Critical Infrastructure | Never remap |
| 11-20 | Databases | Never remap |
| 21-30 | Core Applications | Never remap |
| 31-40 | Monitoring | Never remap |
| 41-50 | Management UIs | Never remap |
| 51-100 | External Services | Always remap |
| 101+ | User Applications | Always remap |

## 📊 Monitoring and Reporting

### Conflict Detection Report
The deployment system generates a conflict report:

```json
{
  "conflict_detection": {
    "timestamp": "2024-01-29T14:30:22Z",
    "conflicts_found": 3,
    "conflicts": [
      {
        "port": 80,
        "existing_service": "apache2",
        "existing_pid": 1234,
        "fuzeinfra_service": "nginx",
        "resolution": "remap_existing",
        "new_port": 8080
      }
    ]
  }
}
```

### Health Check Verification
Post-deployment verification ensures all services are healthy:

```bash
# Verify FuzeInfra services
curl -f http://localhost:80         # nginx
curl -f http://localhost:3001       # grafana
pg_isready -h localhost -p 5432     # postgres

# Verify remapped services
curl -f http://localhost:8080       # remapped apache
```

## 🔍 Troubleshooting

### Common Issues

#### 1. Port Still in Use After Remapping
```bash
# Check what's using the port
sudo lsof -i :80
sudo netstat -tulpn | grep :80

# Force kill if necessary
sudo fuser -k 80/tcp
```

#### 2. Service Won't Start on New Port
```bash
# Check service configuration
docker logs existing-nginx
systemctl status apache2

# Verify new port is available
nc -zv localhost 8080
```

#### 3. Application Can't Find Remapped Service
```bash
# Update application configuration
# Example: database connection string
DATABASE_URL=postgresql://user:pass@localhost:5433/db

# Use environment variables for flexibility
POSTGRES_PORT=5433
```

#### 4. Health Checks Failing
```bash
# Extended health check timeout
HEALTH_CHECK_TIMEOUT=600

# Manual service verification
docker exec fuzeinfra-postgres pg_isready
docker exec fuzeinfra-redis redis-cli ping
```

### Recovery Procedures

#### 1. Rollback Port Changes
```bash
# Use the rollback script
./scripts/deploy/rollback.sh [backup_timestamp]

# Or manual rollback
docker stop fuzeinfra-nginx
docker start existing-nginx  # Will reclaim port 80
```

#### 2. Manual Port Resolution
```bash
# Stop all services
docker-compose -f docker-compose.FuzeInfra.yml down

# Manually configure ports
# Edit docker-compose.yml port mappings
# Restart with custom configuration
```

## 📚 Best Practices

### 1. Pre-deployment Planning
- **Audit existing services** before FuzeInfra deployment
- **Document current port usage** for reference
- **Plan remapping strategy** for critical services
- **Test in staging environment** first

### 2. Graceful Migration
- **Use differential deployment** to minimize downtime
- **Backup configurations** before making changes
- **Update monitoring** to track new ports
- **Notify stakeholders** of port changes

### 3. Documentation
- **Maintain port registry** of all services
- **Document remapping decisions** in deployment reports
- **Update application configs** to reference new ports
- **Create rollback procedures** for each service

### 4. Monitoring
- **Monitor service health** after port changes
- **Set up alerts** for port conflicts
- **Log all port remapping** activities
- **Regular audits** of port usage

## 🔐 Security Considerations

### Firewall Rules
Update firewall rules for remapped services:

```bash
# Allow new ports for remapped services
sudo ufw allow 8080/tcp  # remapped nginx
sudo ufw allow 5433/tcp  # remapped postgres

# Consider removing old rules if no longer needed
sudo ufw delete allow 80/tcp  # if completely migrated
```

### Access Control
Maintain security for remapped services:

- **Update load balancer** configurations
- **Modify reverse proxy** rules
- **Update DNS records** if necessary
- **Review authentication** settings

### Network Segmentation
Consider network isolation:

- **Separate networks** for different service tiers
- **Internal-only access** for remapped databases
- **Public proxy** for web services only

## 📞 Support and Escalation

### When to Seek Help
- **Multiple service failures** after port remapping
- **Data integrity concerns** with database port changes
- **Production downtime** due to conflicts
- **Security implications** of port exposure

### Escalation Procedure
1. **Immediate rollback** if production is affected
2. **Document the issue** with detailed logs
3. **Contact system administrators** for complex conflicts
4. **Review deployment strategy** for future improvements

---

This document ensures that FuzeInfra can be deployed safely on shared infrastructure while maintaining service availability and data integrity. Regular review and updates of these procedures help maintain a smooth deployment process.