# Cloudflare DNS Configuration for FuzeInfra
# Replaces Route53 DNS management since domain is hosted on Cloudflare

# Cloudflare provider is added to main.tf required_providers block

# Configure Cloudflare provider with API token
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# Get Cloudflare zone information for fuzefront.com
data "cloudflare_zones" "fuzefront" {
  filter {
    name = var.domain_name
  }
}

# Create CNAME record for infra subdomain pointing to ALB
resource "cloudflare_record" "infra" {
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "infra"
  content = aws_lb.fuzeinfra_alb.dns_name
  type    = "CNAME"
  ttl     = 300
  comment = "FuzeInfra ALB - Managed by Terraform"
  
  # tags = [
  #   "terraform",
  #   "fuzeinfra", 
  #   var.environment
  # ]
}

# Optional: Create CNAME for alternative access patterns
resource "cloudflare_record" "infra_www" {
  count   = var.create_www_subdomain ? 1 : 0
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "www.infra"
  content = "infra.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  comment = "FuzeInfra www subdomain - Managed by Terraform"
  
  # tags = [
  #   "terraform",
  #   "fuzeinfra",
  #   var.environment
  # ]
}

# Create subdomains for Zero Trust secured services - point to ALB
resource "cloudflare_record" "infra_services" {
  for_each = toset([
    "grafana",
    "prometheus", 
    "alertmanager",
    "pgadmin",
    "mongo",
    "rabbitmq",
    "neo4j",
    "airflow",
    "flower",
    "kafka",
    "elastic",
    "chroma",
    "dns",
    "loki",
    "argocd"
  ])
  
  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = "${each.key}.infra"
  content = aws_lb.fuzeinfra_alb.dns_name
  type    = "CNAME"
  ttl     = 300
  comment = "FuzeInfra ${each.key} service via ALB - Managed by Terraform"
  
  # tags = [
  #   "terraform",
  #   "fuzeinfra",
  #   var.environment,
  #   each.key
  # ]
}

# Output DNS information
output "dns_info" {
  description = "DNS configuration information"  
  value = {
    zone_id           = data.cloudflare_zones.fuzefront.zones[0].id
    zone_name         = data.cloudflare_zones.fuzefront.zones[0].name
    infra_record_id   = cloudflare_record.infra.id
    infra_fqdn        = "${cloudflare_record.infra.name}.${var.domain_name}"
    record_value      = cloudflare_record.infra.content
    dns_provider      = "cloudflare"
    target_type       = "alb"
    alb_dns_name      = aws_lb.fuzeinfra_alb.dns_name
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
    
    # Note: Security headers are handled via Cloudflare zone settings
  }
}

# Cloudflare Analytics (optional)
output "cloudflare_analytics" {
  description = "Cloudflare zone analytics information"
  value = {
    zone_id = data.cloudflare_zones.fuzefront.zones[0].id
    zone_name = data.cloudflare_zones.fuzefront.zones[0].name
  }
}