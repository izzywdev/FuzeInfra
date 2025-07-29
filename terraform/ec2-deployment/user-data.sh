#!/bin/bash
# FuzeInfra EC2 Instance Initialization Script
# This script sets up a new EC2 instance for FuzeInfra deployment

set -euo pipefail

# Configuration from Terraform template
INSTANCE_NAME="${instance_name}"
ENVIRONMENT_NAME="${environment}"

# Logging setup
LOG_FILE="/var/log/fuzeinfra-init.log"
exec > >(tee -a $LOG_FILE)
exec 2>&1

echo "=========================================="
echo "FuzeInfra Instance Initialization Started"
echo "Instance: $INSTANCE_NAME"
echo "Environment: $ENVIRONMENT_NAME"
echo "Timestamp: $(date)"
echo "=========================================="

# Update system packages
echo "📦 Updating system packages..."
apt-get update -y
apt-get upgrade -y

# Install essential packages
echo "🔧 Installing essential packages..."
apt-get install -y \
    curl \
    wget \
    git \
    unzip \
    software-properties-common \
    apt-transport-https \
    ca-certificates \
    gnupg \
    lsb-release \
    htop \
    vim \
    tree \
    jq \
    netcat-openbsd \
    dnsutils

# Install Docker
echo "🐳 Installing Docker..."
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker
systemctl start docker
systemctl enable docker

# Add ubuntu user to docker group
usermod -aG docker ubuntu

# Install Docker Compose (standalone)
echo "🔨 Installing Docker Compose..."
DOCKER_COMPOSE_VERSION="v2.24.5"
curl -L "https://github.com/docker/compose/releases/download/$DOCKER_COMPOSE_VERSION/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose

# Setup additional Docker volume if attached
echo "💾 Setting up Docker data volume..."
if [[ -b /dev/nvme1n1 ]] || [[ -b /dev/xvdf ]]; then
    # Determine device name
    DEVICE=""
    if [[ -b /dev/nvme1n1 ]]; then
        DEVICE="/dev/nvme1n1"
    elif [[ -b /dev/xvdf ]]; then
        DEVICE="/dev/xvdf"
    fi
    
    if [[ -n "$DEVICE" ]]; then
        echo "Found additional volume: $DEVICE"
        
        # Format and mount additional volume for Docker data
        mkfs.ext4 -F $DEVICE
        mkdir -p /var/lib/docker-data
        mount $DEVICE /var/lib/docker-data
        
        # Add to fstab for persistent mounting
        echo "$DEVICE /var/lib/docker-data ext4 defaults,nofail 0 2" >> /etc/fstab
        
        # Set proper permissions
        chown root:docker /var/lib/docker-data
        chmod 755 /var/lib/docker-data
        
        echo "✅ Docker data volume mounted at /var/lib/docker-data"
    fi
fi

# Install AWS CLI v2
echo "☁️ Installing AWS CLI..."
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
./aws/install
rm -rf aws awscliv2.zip

# Install GitHub CLI
echo "🐙 Installing GitHub CLI..."
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
apt-get update -y
apt-get install -y gh

# Install Terraform
echo "🏗️ Installing Terraform..."
wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor | tee /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/hashicorp.list
apt-get update -y
apt-get install -y terraform

# Create FuzeInfra directory structure
echo "📁 Creating FuzeInfra directory structure..."
mkdir -p /opt/fuzeinfra/{logs,backups,config,keys}
chown -R ubuntu:ubuntu /opt/fuzeinfra
chmod 755 /opt/fuzeinfra
chmod 700 /opt/fuzeinfra/keys

# Setup log rotation for FuzeInfra
echo "📝 Setting up log rotation..."
cat > /etc/logrotate.d/fuzeinfra << EOF
/opt/fuzeinfra/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 ubuntu ubuntu
}

/var/log/fuzeinfra-*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
}
EOF

# Configure CloudWatch Agent (optional)
echo "📊 Installing CloudWatch Agent..."
wget https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb
dpkg -i -E amazon-cloudwatch-agent.deb
rm amazon-cloudwatch-agent.deb

# Create CloudWatch Agent configuration
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << EOF
{
    "agent": {
        "metrics_collection_interval": 300,
        "run_as_user": "cwagent"
    },
    "logs": {
        "logs_collected": {
            "files": {
                "collect_list": [
                    {
                        "file_path": "/var/log/fuzeinfra-init.log",
                        "log_group_name": "/aws/ec2/fuzeinfra-$ENVIRONMENT_NAME",
                        "log_stream_name": "{instance_id}/init",
                        "timezone": "UTC"
                    },
                    {
                        "file_path": "/opt/fuzeinfra/logs/*.log",
                        "log_group_name": "/aws/ec2/fuzeinfra-$ENVIRONMENT_NAME",
                        "log_stream_name": "{instance_id}/application",
                        "timezone": "UTC"
                    }
                ]
            }
        }
    },
    "metrics": {
        "namespace": "FuzeInfra/$ENVIRONMENT_NAME",
        "metrics_collected": {
            "cpu": {
                "measurement": [
                    "cpu_usage_idle",
                    "cpu_usage_iowait",
                    "cpu_usage_user",
                    "cpu_usage_system"
                ],
                "metrics_collection_interval": 300,
                "totalcpu": false
            },
            "disk": {
                "measurement": [
                    "used_percent"
                ],
                "metrics_collection_interval": 300,
                "resources": [
                    "*"
                ]
            },
            "diskio": {
                "measurement": [
                    "io_time"
                ],
                "metrics_collection_interval": 300,
                "resources": [
                    "*"
                ]
            },
            "mem": {
                "measurement": [
                    "mem_used_percent"
                ],
                "metrics_collection_interval": 300
            }
        }
    }
}
EOF

# Setup firewall (UFW)
echo "🔥 Configuring firewall..."
ufw --force enable

# Allow SSH
ufw allow 22/tcp

# Allow FuzeInfra ports
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 53/udp    # DNS
ufw allow 5432/tcp  # PostgreSQL
ufw allow 27017/tcp # MongoDB
ufw allow 6379/tcp  # Redis
ufw allow 3001/tcp  # Grafana
ufw allow 9090/tcp  # Prometheus
ufw allow 8082/tcp  # Airflow

echo "✅ Firewall configured with FuzeInfra ports"

# Setup system limits for containers
echo "⚙️ Configuring system limits..."
cat >> /etc/security/limits.conf << EOF
* soft nofile 65536
* hard nofile 65536
* soft nproc 32768
* hard nproc 32768
EOF

# Configure sysctl for better container performance
cat >> /etc/sysctl.conf << EOF
# FuzeInfra container optimizations
vm.max_map_count=262144
fs.file-max=2097152
net.core.somaxconn=32768
net.ipv4.ip_local_port_range=1024 65535
net.ipv4.tcp_max_syn_backlog=8192
EOF

sysctl -p

# Create startup script for FuzeInfra
echo "🚀 Creating FuzeInfra startup script..."
cat > /opt/fuzeinfra/startup.sh << 'EOF'
#!/bin/bash
# FuzeInfra Auto-startup Script

set -euo pipefail

cd /opt/fuzeinfra

echo "$(date): Starting FuzeInfra services..." >> logs/startup.log

# Create Docker network if it doesn't exist
if ! docker network inspect FuzeInfra >/dev/null 2>&1; then
    echo "Creating FuzeInfra Docker network..."
    docker network create FuzeInfra
fi

# Start services if docker-compose file exists
if [[ -f "docker-compose.FuzeInfra.yml" ]]; then
    echo "Starting FuzeInfra services..."
    docker-compose -f docker-compose.FuzeInfra.yml up -d
    echo "$(date): FuzeInfra services started" >> logs/startup.log
else
    echo "$(date): No docker-compose file found, waiting for deployment..." >> logs/startup.log
fi
EOF

chmod +x /opt/fuzeinfra/startup.sh

# Create systemd service for FuzeInfra
cat > /etc/systemd/system/fuzeinfra.service << EOF
[Unit]
Description=FuzeInfra Platform Services
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/fuzeinfra
ExecStart=/opt/fuzeinfra/startup.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable fuzeinfra

# Setup health check script
echo "🏥 Creating health check script..."
cat > /opt/fuzeinfra/health-check.sh << 'EOF'
#!/bin/bash
# FuzeInfra Health Check Script

HEALTH_FILE="/opt/fuzeinfra/logs/health.log"
DATE=$(date)

echo "[$DATE] Running health checks..." >> $HEALTH_FILE

# Check Docker
if systemctl is-active --quiet docker; then
    echo "[$DATE] ✅ Docker is running" >> $HEALTH_FILE
else
    echo "[$DATE] ❌ Docker is not running" >> $HEALTH_FILE
fi

# Check disk space
DISK_USAGE=$(df / | awk 'NR==2 {print $5}' | sed 's/%//')
if [[ $DISK_USAGE -lt 90 ]]; then
    echo "[$DATE] ✅ Disk usage: ${DISK_USAGE}%" >> $HEALTH_FILE
else
    echo "[$DATE] ⚠️ Disk usage high: ${DISK_USAGE}%" >> $HEALTH_FILE
fi

# Check memory
MEMORY_USAGE=$(free | awk 'NR==2{printf "%d", $3*100/$2}')
if [[ $MEMORY_USAGE -lt 90 ]]; then
    echo "[$DATE] ✅ Memory usage: ${MEMORY_USAGE}%" >> $HEALTH_FILE
else
    echo "[$DATE] ⚠️ Memory usage high: ${MEMORY_USAGE}%" >> $HEALTH_FILE
fi

echo "[$DATE] Health check completed" >> $HEALTH_FILE
EOF

chmod +x /opt/fuzeinfra/health-check.sh

# Setup cron job for health checks
echo "⏰ Setting up health check cron job..."
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/fuzeinfra/health-check.sh") | crontab -

# Create instance metadata file
echo "📋 Creating instance metadata..."
cat > /opt/fuzeinfra/instance-metadata.json << EOF
{
    "instance_name": "$INSTANCE_NAME",
    "environment": "$ENVIRONMENT_NAME",
    "initialized_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "initialization_version": "1.0.0",
    "ami_id": "$(curl -s http://169.254.169.254/latest/meta-data/ami-id)",
    "instance_id": "$(curl -s http://169.254.169.254/latest/meta-data/instance-id)",
    "instance_type": "$(curl -s http://169.254.169.254/latest/meta-data/instance-type)",
    "availability_zone": "$(curl -s http://169.254.169.254/latest/meta-data/placement/availability-zone)",
    "local_ipv4": "$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)",
    "public_ipv4": "$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"
}
EOF

# Set up automatic security updates
echo "🔐 Configuring automatic security updates..."
apt-get install -y unattended-upgrades
echo 'Unattended-Upgrade::Automatic-Reboot "false";' >> /etc/apt/apt.conf.d/50unattended-upgrades

# Final cleanup
echo "🧹 Performing cleanup..."
apt-get autoremove -y
apt-get autoclean
rm -rf /tmp/*

# Signal completion
echo "=========================================="
echo "✅ FuzeInfra Instance Initialization Complete"
echo "Instance: $INSTANCE_NAME"
echo "Environment: $ENVIRONMENT_NAME"
echo "Completed: $(date)"
echo "=========================================="

# Create completion marker
touch /opt/fuzeinfra/.initialized
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /opt/fuzeinfra/.initialization-timestamp

# Send completion signal to CloudWatch (if configured)
if command -v aws &> /dev/null; then
    aws cloudwatch put-metric-data \
        --namespace "FuzeInfra/$ENVIRONMENT_NAME" \
        --metric-data MetricName=InstanceInitialized,Value=1,Unit=Count \
        --region us-east-1 2>/dev/null || true
fi

echo "🎉 Instance is ready for FuzeInfra deployment!"

exit 0