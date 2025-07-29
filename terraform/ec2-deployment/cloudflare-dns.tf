# Cloudflare DNS Configuration for FuzeInfra
# Replaces Route53 DNS management since domain is hosted on Cloudflare

# Cloudflare provider is added to main.tf required_providers block

# Configure Cloudflare provider
provider "cloudflare" {
  email   = var.cloudflare_email
  api_key = var.cloudflare_api_key
}

# Get Cloudflare zone information for fuzefront.com
data "cloudflare_zones" "fuzefront" {
  filter {
    name = var.domain_name
  }
}

# Create A record for infra subdomain pointing to EC2 instance
resource "cloudflare_record" "infra" {
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "infra"
  value   = local.instance_public_ip
  type    = "A"
  ttl     = 300
  comment = "FuzeInfra EC2 instance - Managed by Terraform"
  
  tags = {
    "managed-by" = "terraform"
    "project"    = "fuzeinfra"
    "environment" = var.environment
  }
}

# Optional: Create CNAME for alternative access patterns
resource "cloudflare_record" "infra_www" {
  count   = var.create_www_subdomain ? 1 : 0
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "www.infra"
  value   = "infra.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  comment = "FuzeInfra www subdomain - Managed by Terraform"
  
  tags = {
    "managed-by" = "terraform"
    "project"    = "fuzeinfra"
    "environment" = var.environment
  }
}

# Create additional subdomains for specific services if needed
resource "cloudflare_record" "infra_services" {
  for_each = var.create_service_subdomains ? toset([
    "grafana",
    "prometheus", 
    "airflow",
    "mongo"
  ]) : []
  
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "${each.key}.infra"
  value   = "infra.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  comment = "FuzeInfra ${each.key} service subdomain - Managed by Terraform"
  
  tags = {
    "managed-by" = "terraform"
    "project"    = "fuzeinfra"
    "environment" = var.environment
    "service"    = each.key
  }
}

# Output DNS information
output "dns_info" {
  description = "DNS configuration information"
  value = {
    zone_id           = data.cloudflare_zones.fuzefront.zones[0].id
    zone_name         = data.cloudflare_zones.fuzefront.zones[0].name
    infra_record_id   = cloudflare_record.infra.id
    infra_fqdn        = "${cloudflare_record.infra.name}.${cloudflare_record.infra.zone_name}"
    record_value      = cloudflare_record.infra.value
    dns_provider      = "cloudflare"
  }
}

# Cloudflare Zone Settings for Security and Performance
resource "cloudflare_zone_settings_override" "fuzefront_settings" {
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  
  settings {
    # SSL/TLS Configuration
    ssl = "strict"
    tls_1_3 = "on"
    automatic_https_rewrites = "on"
    
    # Security Settings
    security_level = "medium"
    challenge_ttl = 1800
    
    # Performance Settings
    minify {
      css  = "on"
      js   = "on"
      html = "on"
    }
    
    brotli = "on"
    
    # Always use HTTPS
    always_use_https = "on"
    
    # Development mode (can be turned off in production)
    development_mode = var.environment == "development" ? "on" : "off"
  }
}

# Page Rules for FuzeInfra subdomain (if needed)
resource "cloudflare_page_rule" "infra_security" {
  zone_id  = data.cloudflare_zones.fuzefront.zones[0].id
  target   = "infra.${var.domain_name}/*"
  priority = 1
  
  actions {
    ssl = "strict"
    always_use_https = true
    
    # Security headers
    security_header {
      enabled = true
      preload = true
      max_age = 86400
      include_subdomains = true
      nosniff = true
    }
  }
}

# Cloudflare Analytics (optional)
output "cloudflare_analytics" {
  description = "Cloudflare zone analytics information"
  value = {
    zone_id = data.cloudflare_zones.fuzefront.zones[0].id
    zone_status = data.cloudflare_zones.fuzefront.zones[0].status
    zone_paused = data.cloudflare_zones.fuzefront.zones[0].paused
    name_servers = data.cloudflare_zones.fuzefront.zones[0].name_servers
  }
}