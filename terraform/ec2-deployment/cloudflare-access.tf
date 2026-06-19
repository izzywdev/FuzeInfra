# Cloudflare Zero Trust Access Configuration
# This creates secure access policies for each FuzeInfra service

# Variables for access control
variable "admin_emails" {
  description = "List of admin email addresses for full access"
  type        = list(string)
  default     = ["izzy.weinberg@gmail.com"]
}

variable "developer_emails" {
  description = "List of developer email addresses for development tools access"
  type        = list(string)
  default     = ["izzy.weinberg@gmail.com"]
}

# Main FuzeInfra Access Application
resource "cloudflare_zero_trust_access_application" "infra_main" {
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra Main Dashboard"
  domain           = "infra.${var.domain_name}"
  type             = "self_hosted"
  logo_url         = "https://fuzefront.com/logo.png"
  app_launcher_visible = true
  
  # tags = [
  #   "production", 
  #   "fuzeinfra",
  #   "main"
  # ]
}

# Main FuzeInfra Access Policy - Restricted to admins
resource "cloudflare_zero_trust_access_policy" "infra_main_policy" {
  application_id = cloudflare_zero_trust_access_application.infra_main.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "FuzeInfra Main Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.admin_emails
  }
}

# Monitoring Services - Admin Access Only
resource "cloudflare_zero_trust_access_application" "monitoring_services" {
  for_each = toset(["grafana", "prometheus", "alertmanager", "loki"])
  
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra ${title(each.key)}"
  domain           = "${each.key}.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "monitoring",
  #   "admin-only", 
  #   each.key
  # ]
}

resource "cloudflare_zero_trust_access_policy" "monitoring_policy" {
  for_each = cloudflare_zero_trust_access_application.monitoring_services
  
  application_id = each.value.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "${title(each.key)} Admin Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.admin_emails
  }
}

# Database Management Interfaces - Admin Access Only
resource "cloudflare_zero_trust_access_application" "database_services" {
  for_each = toset(["pgadmin", "mongo", "rabbitmq", "neo4j"])
  
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra ${title(each.key)} Management"
  domain           = "${each.key}.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "database",
  #   "admin-only",
  #   each.key
  # ]
}

resource "cloudflare_zero_trust_access_policy" "database_policy" {
  for_each = cloudflare_zero_trust_access_application.database_services
  
  application_id = each.value.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "${title(each.key)} Admin Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.admin_emails
  }
}

# Workflow & Development Tools - Developer Access
resource "cloudflare_zero_trust_access_application" "workflow_services" {
  for_each = toset(["airflow", "flower", "kafka"])
  
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra ${title(each.key)}"
  domain           = "${each.key}.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "workflow",
  #   "development",
  #   each.key
  # ]
}

resource "cloudflare_zero_trust_access_policy" "workflow_policy" {
  for_each = cloudflare_zero_trust_access_application.workflow_services
  
  application_id = each.value.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "${title(each.key)} Developer Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.developer_emails
  }
}

# Search & AI Services - Developer Access
resource "cloudflare_zero_trust_access_application" "ai_services" {
  for_each = toset(["elastic", "chroma"])
  
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra ${title(each.key)}"
  domain           = "${each.key}.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "ai-search",
  #   "development",
  #   each.key
  # ]
}

resource "cloudflare_zero_trust_access_policy" "ai_policy" {
  for_each = cloudflare_zero_trust_access_application.ai_services
  
  application_id = each.value.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "${title(each.key)} Developer Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.developer_emails
  }
}

# DNS Management - Admin Only
resource "cloudflare_zero_trust_access_application" "dns_service" {
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra DNS Management"
  domain           = "dns.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "infrastructure",
  #   "admin-only",
  #   "dns"
  # ]
}

# ArgoCD GitOps & Health Monitoring - Admin Only
resource "cloudflare_zero_trust_access_application" "argocd_service" {
  zone_id          = data.cloudflare_zones.fuzefront.zones[0].id
  name             = "FuzeInfra ArgoCD - GitOps & Health"
  domain           = "argocd.infra.${var.domain_name}"
  type             = "self_hosted"
  app_launcher_visible = true
  
  # tags = [
  #   "gitops",
  #   "health-monitoring", 
  #   "admin-only",
  #   "argocd"
  # ]
}

resource "cloudflare_zero_trust_access_policy" "dns_policy" {
  application_id = cloudflare_zero_trust_access_application.dns_service.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "DNS Admin Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.admin_emails
  }
}

resource "cloudflare_zero_trust_access_policy" "argocd_policy" {
  application_id = cloudflare_zero_trust_access_application.argocd_service.id
  zone_id        = data.cloudflare_zones.fuzefront.zones[0].id
  name           = "ArgoCD Admin Access"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.admin_emails
  }
}

# Output the access application URLs
output "zero_trust_applications" {
  description = "Cloudflare Zero Trust Access application URLs"
  value = {
    main_dashboard = "https://infra.${var.domain_name}"
    
    monitoring = {
      grafana      = "https://grafana.infra.${var.domain_name}"
      prometheus   = "https://prometheus.infra.${var.domain_name}"
      alertmanager = "https://alertmanager.infra.${var.domain_name}"
      loki         = "https://loki.infra.${var.domain_name}"
    }
    
    databases = {
      pgadmin  = "https://pgadmin.infra.${var.domain_name}"
      mongo    = "https://mongo.infra.${var.domain_name}"
      rabbitmq = "https://rabbitmq.infra.${var.domain_name}" 
      neo4j    = "https://neo4j.infra.${var.domain_name}"
    }
    
    workflow = {
      airflow = "https://airflow.infra.${var.domain_name}"
      flower  = "https://flower.infra.${var.domain_name}"
      kafka   = "https://kafka.infra.${var.domain_name}"
    }
    
    ai_search = {
      elasticsearch = "https://elastic.infra.${var.domain_name}"
      chromadb     = "https://chroma.infra.${var.domain_name}"
    }
    
    infrastructure = {
      dns    = "https://dns.infra.${var.domain_name}"
      argocd = "https://argocd.infra.${var.domain_name}"
    }
  }
}

# Output access control summary
output "access_control_summary" {
  description = "Summary of Zero Trust access control configuration"
  value = {
    admin_controlled_services = [
      "Main Dashboard",
      "Grafana", 
      "Prometheus",
      "Alertmanager",
      "Loki",
      "MongoDB Express",
      "RabbitMQ Management",
      "Neo4j Browser",
      "DNS Management",
      "ArgoCD GitOps & Health"
    ]
    
    developer_accessible_services = [
      "Airflow",
      "Flower", 
      "Kafka UI",
      "Elasticsearch",
      "ChromaDB"
    ]
    
    admin_emails = var.admin_emails
    developer_emails = var.developer_emails
    
    total_applications = 15
    security_level = "Zero Trust"
  }
}