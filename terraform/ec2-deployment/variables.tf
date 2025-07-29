# Terraform Variables for FuzeInfra EC2 Deployment

variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (production, staging, etc.)"
  type        = string
  default     = "production"
}

variable "instance_name" {
  description = "Name tag of the existing EC2 instance to deploy to"
  type        = string
  default     = "fuzefront-infra"
}

variable "domain_name" {
  description = "Domain name for DNS records"
  type        = string
  default     = "fuzefront.com"
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access FuzeInfra services"
  type        = list(string)
  default     = ["0.0.0.0/0"] # Restrict this in production
}

variable "create_instance" {
  description = "Whether to create a new EC2 instance (true) or use existing (false)"
  type        = bool
  default     = false
}

variable "create_eip" {
  description = "Whether to create and associate an Elastic IP"
  type        = bool
  default     = false # Set to true if instance doesn't have EIP
}

variable "instance_type" {
  description = "EC2 instance type for new instances"
  type        = string
  default     = "t3.large"
}

variable "availability_zone" {
  description = "Availability zone for new instances"
  type        = string
  default     = "us-east-1a"
}

variable "root_volume_size" {
  description = "Root volume size in GB for new instances"
  type        = number
  default     = 30
}

variable "docker_volume_size" {
  description = "Docker data volume size in GB for new instances"
  type        = number
  default     = 50
}

variable "enable_monitoring" {
  description = "Whether to enable detailed monitoring and CloudWatch alarms"
  type        = bool
  default     = true
}

variable "sns_topic_arn" {
  description = "SNS topic ARN for CloudWatch alarms"
  type        = string
  default     = ""
}

variable "attach_iam_profile" {
  description = "Whether to attach IAM instance profile (set false if already attached)"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "create_artifacts_bucket" {
  description = "Whether to create S3 bucket for deployment artifacts"
  type        = bool
  default     = true
}

# FuzeInfra Configuration Variables
variable "fuzeinfra_config" {
  description = "FuzeInfra configuration settings"
  type = object({
    project_name         = string
    network_name         = string
    postgres_port        = number
    mongodb_port         = number
    redis_port           = number
    grafana_port         = number
    prometheus_port      = number
    nginx_http_port      = number
    nginx_https_port     = number
    dns_port             = number
    airflow_port         = number
    enable_https         = bool
    enable_tunnel        = bool
    enable_monitoring    = bool
  })
  default = {
    project_name         = "fuzeinfra"
    network_name         = "FuzeInfra"
    postgres_port        = 5432
    mongodb_port         = 27017
    redis_port           = 6379
    grafana_port         = 3001
    prometheus_port      = 9090
    nginx_http_port      = 80
    nginx_https_port     = 443
    dns_port             = 53
    airflow_port         = 8082
    enable_https         = true
    enable_tunnel        = true
    enable_monitoring    = true
  }
}

# Port Conflict Management
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
    # Example: If existing nginx is on port 80, remap it
    "existing-nginx" = {
      service_name = "existing-nginx"
      old_port     = 80
      new_port     = 8080
      protocol     = "tcp"
      priority     = 100
    }
    # FuzeInfra services have priority 1-50
  }
}

# Deployment Configuration
variable "deployment_config" {
  description = "Deployment configuration settings"
  type = object({
    docker_compose_file     = string
    environment_file        = string
    backup_before_deploy    = bool
    health_check_timeout    = number
    rollback_on_failure     = bool
    notification_webhook    = string
  })
  default = {
    docker_compose_file     = "docker-compose.FuzeInfra.yml"
    environment_file        = ".env.production"
    backup_before_deploy    = true
    health_check_timeout    = 300
    rollback_on_failure     = true
    notification_webhook    = ""
  }
}

# SSL/TLS Configuration
variable "ssl_config" {
  description = "SSL/TLS configuration"
  type = object({
    cert_provider     = string # "letsencrypt", "cloudflare", "manual"
    domain_names      = list(string)
    cloudflare_email  = string
    cloudflare_api_key = string
    auto_renewal      = bool
  })
  default = {
    cert_provider     = "letsencrypt"
    domain_names      = ["infra.fuzefront.com"]
    cloudflare_email  = ""
    cloudflare_api_key = ""
    auto_renewal      = true
  }
}

# Monitoring Configuration
variable "monitoring_config" {
  description = "Monitoring and alerting configuration"
  type = object({
    enable_cloudwatch_logs    = bool
    enable_prometheus_remote  = bool
    alert_manager_webhook     = string
    grafana_admin_password    = string
    retention_days           = number
  })
  default = {
    enable_cloudwatch_logs    = true
    enable_prometheus_remote  = false
    alert_manager_webhook     = ""
    grafana_admin_password    = "" # Will be auto-generated if empty
    retention_days           = 30
  }
}

# Backup Configuration
variable "backup_config" {
  description = "Backup configuration for data persistence"
  type = object({
    enable_s3_backup     = bool
    backup_schedule      = string
    retention_days       = number
    s3_bucket_name       = string
    backup_encryption    = bool
  })
  default = {
    enable_s3_backup     = true
    backup_schedule      = "0 2 * * *" # Daily at 2 AM
    retention_days       = 30
    s3_bucket_name       = ""
    backup_encryption    = true
  }
}