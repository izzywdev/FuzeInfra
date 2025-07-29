# FuzeInfra Deployment Scripts

This directory contains deployment and rollback scripts for FuzeInfra production environments.

## 📁 Files

- `deploy-production.sh` - Main production deployment script
- `rollback.sh` - Rollback script for restoring from backups
- `README.md` - This documentation file

## 🚀 Production Deployment

### Prerequisites

1. **SSH Access**: Ensure you have SSH access to the target EC2 instance
2. **Environment Variables**: Set required environment variables
3. **SSH Key**: Have the SSH private key for the instance

### Environment Variables

```bash
export INSTANCE_IP="your-instance-ip"
export SSH_KEY_PATH="/path/to/your/ssh-key.pem"
export BACKUP_BEFORE_DEPLOY="true"           # Optional: default true
export HEALTH_CHECK_TIMEOUT="300"            # Optional: default 300 seconds
```

### Deployment Types

#### Differential Deployment (Recommended)
Updates only changed services to minimize downtime:

```bash
./scripts/deploy/deploy-production.sh differential false
```

#### Initial Deployment
Full deployment with all services (use for first-time setup):

```bash
./scripts/deploy/deploy-production.sh initial true
```

#### Force Recreation
Stops and recreates all containers (use when troubleshooting):

```bash
./scripts/deploy/deploy-production.sh differential true
```

### Deployment Process

The deployment script performs the following steps:

1. **Prerequisites Validation**
   - Validates environment variables
   - Tests SSH connectivity
   - Validates Docker Compose configuration

2. **Backup Creation**
   - Creates timestamped backup of Docker volumes
   - Backs up configuration files and environment

3. **File Synchronization**
   - Creates deployment package (excludes .git, node_modules, etc.)
   - Transfers files to target instance
   - Extracts and sets proper permissions

4. **Port Conflict Management**
   - Checks for port conflicts with existing services
   - Stops conflicting services if FORCE_RECREATE=true

5. **Service Deployment**
   - **Differential**: Updates critical services first (postgres, mongodb, redis), then others
   - **Initial**: Deploys all services at once
   - **Rollback**: Calls rollback script

6. **Health Checks**
   - Verifies container status
   - Tests port accessibility
   - Validates service connectivity

7. **Reporting**
   - Generates deployment report with metadata
   - Logs service URLs and access information

## 🔄 Rollback Operations

### List Available Backups

```bash
./scripts/deploy/rollback.sh
```

### Rollback to Specific Backup

```bash
./scripts/deploy/rollback.sh 20240129_143022
```

### Rollback Process

The rollback script performs:

1. **Backup Validation** - Verifies the requested backup exists and is complete
2. **Pre-rollback Backup** - Creates backup of current state before rollback
3. **Service Shutdown** - Gracefully stops all FuzeInfra services
4. **Volume Restoration** - Restores Docker volumes from backup
5. **Configuration Restoration** - Restores config files and environment
6. **Service Startup** - Starts services with restored data
7. **Verification** - Validates all services are healthy after rollback

## 🔧 Configuration Files

### Terraform Variables Example

Copy and customize the Terraform variables:

```bash
cp terraform/ec2-deployment/terraform.tfvars.example terraform/ec2-deployment/terraform.tfvars
# Edit terraform.tfvars with your specific values
```

### Environment File

The deployment process creates a production environment file on the target instance with:

- Database credentials from GitHub Secrets
- SSL/TLS configuration for Let's Encrypt
- Cloudflare Tunnel settings
- Monitoring and security configurations

## 🔒 Security Considerations

### GitHub Secrets Required

Set these secrets in your GitHub repository:

```bash
# AWS Credentials
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY

# SSH Access
EC2_SSH_PRIVATE_KEY

# Database Passwords
POSTGRES_PASSWORD
MONGODB_PASSWORD
GRAFANA_ADMIN_PASSWORD

# SSL/TLS
CLOUDFLARE_EMAIL
CLOUDFLARE_API_KEY

# Tunnel Configuration
CLOUDFLARE_TUNNEL_TOKEN
CLOUDFLARE_TUNNEL_ID

# Webhook Management
GITHUB_TOKEN
GITHUB_WEBHOOK_SECRET
ATLASSIAN_EMAIL
ATLASSIAN_API_TOKEN
ATLASSIAN_INSTANCE

# Security Keys
DNSMASQ_ADMIN_PASSWORD
WEBHOOK_REGISTRY_KEY

# Optional: Notifications
SLACK_WEBHOOK_URL
```

### SSH Key Security

- Use key-based authentication (no passwords)
- Restrict SSH access to specific IP ranges
- Use dedicated deployment keys for CI/CD

### Network Security

- Configure `allowed_cidr_blocks` in Terraform variables
- Use security groups to restrict access
- Enable HTTPS for all web interfaces

## 🔍 Troubleshooting

### Common Issues

1. **SSH Connection Failures**
   ```bash
   # Test SSH connectivity
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "echo 'Connection test'"
   ```

2. **Port Conflicts**
   ```bash
   # Check what's running on conflicting ports
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "sudo netstat -tulpn | grep LISTEN"
   # Use FORCE_RECREATE=true to resolve conflicts
   ```

3. **Service Health Check Failures**
   ```bash
   # Check container logs
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "docker logs fuzeinfra-<service>"
   # Check Docker Compose status
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "cd /opt/fuzeinfra && docker-compose ps"
   ```

4. **Volume Mount Issues**
   ```bash
   # Check volume status
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "docker volume ls"
   # Inspect specific volume
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "docker volume inspect fuzeinfra_postgres_data"
   ```

### Deployment Logs

Deployment logs are available in:
- GitHub Actions workflow logs
- `/var/log/fuzeinfra-deployment.log` on target instance
- Service-specific logs via `docker logs <container>`

### Emergency Procedures

1. **Complete Rollback**
   ```bash
   # List available backups and rollback
   ./scripts/deploy/rollback.sh
   ```

2. **Manual Service Restart**
   ```bash
   ssh -i $SSH_KEY_PATH ubuntu@$INSTANCE_IP "cd /opt/fuzeinfra && docker-compose restart <service>"
   ```

3. **Force Clean Deployment**
   ```bash
   # Stop everything and redeploy
   ./scripts/deploy/deploy-production.sh initial true
   ```

## 📊 Monitoring

After deployment, monitor your services via:

- **Grafana**: https://infra.fuzefront.com:3001
- **Prometheus**: https://infra.fuzefront.com:9090
- **Airflow**: https://infra.fuzefront.com:8082
- **System logs**: CloudWatch and local log aggregation

## 🤝 Best Practices

1. **Always test deployments** in a staging environment first
2. **Use differential deployments** for production updates
3. **Monitor services** during and after deployment
4. **Keep backups** for at least 30 days
5. **Document changes** in deployment reports
6. **Review security settings** regularly
7. **Test rollback procedures** periodically

## 📞 Support

If you encounter issues:

1. Check the troubleshooting section above
2. Review deployment logs in GitHub Actions
3. Examine service logs on the target instance
4. Use rollback if necessary to restore service
5. Open an issue with detailed error information