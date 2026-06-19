# ---------------------------------------------------------------------------
# Cloudflare credentials
# ---------------------------------------------------------------------------
variable "cloudflare_api_token" {
  description = <<-EOT
    Cloudflare API token. Required permissions:
      - Zone > DNS > Edit       (create CNAME records)
      - Account > Cloudflare Tunnel > Edit  (create/configure named tunnel)
      - Account > Access: Apps and Policies > Edit  (create Zero Trust apps)
    Create at: https://dash.cloudflare.com/profile/api-tokens
  EOT
  type      = string
  sensitive = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID — visible in the URL when logged into the Cloudflare dashboard"
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Zone ID for fuzefront.com — visible in the Overview tab of the zone in the Cloudflare dashboard"
  type        = string
}

# ---------------------------------------------------------------------------
# Tunnel / domain config
# ---------------------------------------------------------------------------
variable "tunnel_name" {
  description = "Display name for the Named Tunnel in the Cloudflare Zero Trust dashboard"
  type        = string
  default     = "fuzeinfra-prod"
}

variable "prod_subdomain" {
  description = "Subdomain under the zone root to serve the Contabo cluster. E.g. 'prod' → prod.fuzefront.com"
  type        = string
  default     = "prod"
}

variable "zone_name" {
  description = "Root domain managed in Cloudflare (must match the zone_id above)"
  type        = string
  default     = "fuzefront.com"
}

# ---------------------------------------------------------------------------
# Zero Trust Access — who may reach the admin UIs
# ---------------------------------------------------------------------------
variable "allowed_admin_emails" {
  description = "Email addresses that may pass through Cloudflare Access to the admin UIs (*.prod.fuzefront.com)"
  type        = list(string)
  default     = ["izzy.weinberg@gmail.com"]
}

variable "access_session_duration" {
  description = "How long a Cloudflare Access session lasts before re-authentication is required"
  type        = string
  default     = "24h"
}
