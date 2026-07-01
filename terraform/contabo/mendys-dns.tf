# ---------------------------------------------------------------------------
# DNS records for mendysrobotics.com subdomains
#
# Points to the shared Contabo k3s VPS (161.97.118.134).
# ingress-nginx runs as a LoadBalancer service; klipper-lb binds ports
# 80 and 443 on all cluster nodes.
#
# Records are NOT Cloudflare-proxied (grey cloud) so that Let's Encrypt
# HTTP-01 challenge packets reach the origin ingress-nginx directly.
#
# Required: add mendys_zone_id to terraform/contabo/terraform.tfvars
# (never commit that file — it holds secrets).
# ---------------------------------------------------------------------------

variable "mendys_zone_id" {
  description = "Cloudflare zone ID for mendysrobotics.com"
  type        = string
  default     = ""
}

locals {
  mendys_dns_enabled = local.cloudflare_enabled && var.mendys_zone_id != ""
}

resource "cloudflare_record" "mendys_live" {
  count   = local.mendys_dns_enabled ? 1 : 0
  zone_id = var.mendys_zone_id
  name    = "live"
  value   = "161.97.118.134"
  type    = "A"
  proxied = false
  ttl     = 300
}

resource "cloudflare_record" "mendys_marketplace" {
  count   = local.mendys_dns_enabled ? 1 : 0
  zone_id = var.mendys_zone_id
  name    = "marketplace"
  value   = "161.97.118.134"
  type    = "A"
  proxied = false
  ttl     = 300
}

resource "cloudflare_record" "mendys_wp" {
  count   = local.mendys_dns_enabled ? 1 : 0
  zone_id = var.mendys_zone_id
  name    = "wp"
  value   = "161.97.118.134"
  type    = "A"
  proxied = false
  ttl     = 300
}

resource "cloudflare_record" "mendys_api_live" {
  count   = local.mendys_dns_enabled ? 1 : 0
  zone_id = var.mendys_zone_id
  name    = "api.live"
  value   = "161.97.118.134"
  type    = "A"
  proxied = false
  ttl     = 300
}
