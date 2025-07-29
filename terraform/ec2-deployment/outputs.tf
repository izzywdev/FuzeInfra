# Terraform Outputs for FuzeInfra EC2 Deployment

output "instance_id" {
  description = "ID of the EC2 instance"
  value       = local.instance_id
}

output "instance_public_ip" {
  description = "Public IP address of the EC2 instance"
  value       = local.instance_public_ip
}

output "instance_private_ip" {
  description = "Private IP address of the EC2 instance"
  value       = local.instance_private_ip
}

output "security_group_id" {
  description = "ID of the FuzeInfra security group"
  value       = aws_security_group.fuzeinfra.id
}

output "dns_record" {
  description = "DNS record for FuzeInfra"
  value       = aws_route53_record.infra.fqdn
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group name"
  value       = aws_cloudwatch_log_group.fuzeinfra.name
}

output "iam_role_arn" {
  description = "ARN of the IAM role for the instance"
  value       = aws_iam_role.fuzeinfra_instance.arn
}

output "artifacts_bucket" {
  description = "S3 bucket for deployment artifacts"
  value       = var.create_artifacts_bucket ? aws_s3_bucket.fuzeinfra_artifacts[0].bucket : null
}

# FuzeInfra Service URLs
output "service_urls" {
  description = "URLs for FuzeInfra services"
  value = {
    main_url           = "https://${aws_route53_record.infra.fqdn}"
    grafana_url        = "https://${aws_route53_record.infra.fqdn}:${var.fuzeinfra_config.grafana_port}"
    prometheus_url     = "https://${aws_route53_record.infra.fqdn}:${var.fuzeinfra_config.prometheus_port}"
    airflow_url        = "https://${aws_route53_record.infra.fqdn}:${var.fuzeinfra_config.airflow_port}"
    mongo_express_url  = "https://${aws_route53_record.infra.fqdn}:8081"
    rabbitmq_url       = "https://${aws_route53_record.infra.fqdn}:15672"
  }
}

# Port Configuration for Documentation
output "port_configuration" {
  description = "Port configuration for FuzeInfra services"
  value = {
    reserved_ports = {
      http           = var.fuzeinfra_config.nginx_http_port
      https          = var.fuzeinfra_config.nginx_https_port
      dns            = var.fuzeinfra_config.dns_port
      postgres       = var.fuzeinfra_config.postgres_port
      mongodb        = var.fuzeinfra_config.mongodb_port
      redis          = var.fuzeinfra_config.redis_port
      grafana        = var.fuzeinfra_config.grafana_port
      prometheus     = var.fuzeinfra_config.prometheus_port
      airflow        = var.fuzeinfra_config.airflow_port
    }
    remapped_services = var.port_remapping_rules
  }
}

# Deployment Information
output "deployment_info" {
  description = "Deployment configuration information"
  value = {
    environment        = var.environment
    instance_name      = var.instance_name
    domain            = var.domain_name
    terraform_version = "~> 1.0"
    aws_region        = var.aws_region
    deployment_date   = timestamp()
  }
}

# Network Information
output "network_info" {
  description = "Network configuration information"
  value = {
    vpc_id           = local.vpc_id
    subnet_id        = local.subnet_id
    vpc_cidr         = local.vpc_cidr
    availability_zone = local.availability_zone
  }
}

# Security Information
output "security_info" {
  description = "Security configuration information"
  value = {
    security_group_id = aws_security_group.fuzeinfra.id
    allowed_cidrs     = var.allowed_cidr_blocks
    ssl_enabled       = var.fuzeinfra_config.enable_https
    cert_provider     = var.ssl_config.cert_provider
  }
}

# Monitoring Information
output "monitoring_info" {
  description = "Monitoring configuration information"
  value = {
    cloudwatch_logs     = aws_cloudwatch_log_group.fuzeinfra.name
    monitoring_enabled  = var.fuzeinfra_config.enable_monitoring
    log_retention_days  = var.log_retention_days
    grafana_enabled     = var.fuzeinfra_config.enable_monitoring
    prometheus_enabled  = var.fuzeinfra_config.enable_monitoring
  }
}

# Connection Information for CI/CD
output "connection_info" {
  description = "Connection information for CI/CD pipelines"
  value = {
    ssh_host         = aws_route53_record.infra.fqdn
    ssh_user         = "ubuntu" # Adjust based on your AMI
    deployment_path  = "/opt/fuzeinfra"
    docker_socket    = "/var/run/docker.sock"
    compose_file     = var.deployment_config.docker_compose_file
    environment_file = var.deployment_config.environment_file
  }
  sensitive = false
}