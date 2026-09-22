# ---------------------------------------------------------------------------
# ACTIVE-ACTIVE external k3s API HA + load balancing — the DNS + tls-san half.
#
# Each durable control plane owns its own Contabo additional IP (a VIP) and serves
# the API on it; api.<domain> round-robins A records across all the VIPs so
# external kubectl / CD / consumers load-balance across every live node. keepalived
# (helm/fuzeinfra/templates/api-vip-keepalived.yaml) moves a dead node's VIP to a
# survivor via the Contabo VIP API (VERIFIED live 2026-09-22), so a round-robin
# record never resolves to a black hole.
#
# WHAT TERRAFORM DOES HERE: publishes the round-robin A records and adds every VIP
# to every apiserver's tls-san (control-planes.tf). It does NOT order the IPs — that
# is a panel purchase (Contabo has no create API for additional IPs, verified). Set
# api_vip_addresses to the ordered IPs.
#
# api_vip_addresses MUST match the apiVip.vips[].address list in
# helm/fuzeinfra/values-contabo.yaml (two sources: Terraform owns DNS + tls-san,
# Helm owns keepalived). GATED: empty list / disabled => everything inert.
# See docs/runbooks/api-floating-vip.md.
# ---------------------------------------------------------------------------

variable "api_vip_enabled" {
  description = "Publish the round-robin VIP DNS records and add the VIPs to each apiserver tls-san. Order the additional IPs first and list them in api_vip_addresses."
  type        = bool
  default     = false
}

variable "api_vip_addresses" {
  description = "The ordered Contabo additional IPs used as the active-active API VIPs (one per core node). Must match apiVip.vips[].address in values-contabo.yaml. Empty leaves everything inert."
  type        = list(string)
  default     = []
}

variable "api_vip_hostname_label" {
  description = "Label (relative to the prod subdomain) for the round-robin VIP DNS records. Full host = <label>.<prod_subdomain>.<zone_name>."
  type        = string
  default     = "api"
}

locals {
  api_vip_active = local.cloudflare_enabled && var.api_vip_enabled && length(var.api_vip_addresses) > 0
  api_vip_host   = "${var.api_vip_hostname_label}.${local.prod_domain}"
}

# DNS: api.prod.fuzefront.com -> one A record PER VIP (round-robin), DNS-ONLY.
#
# NOT proxied: the k8s API is raw TLS on 6443 with client-cert/token auth that
# Cloudflare's HTTP proxy cannot pass. Multiple A records on the same name make DNS
# round-robin across the live VIPs; keepalived keeps every advertised IP on a live
# node, so a dead node's record is served by whoever adopted its VIP. More specific
# than the *.prod / * proxied wildcards, so it resolves straight to the VIPs.
resource "cloudflare_record" "api_vip" {
  for_each = nonsensitive(local.api_vip_active) ? toset(var.api_vip_addresses) : toset([])
  zone_id  = var.cloudflare_zone_id
  name     = "${var.api_vip_hostname_label}.${var.prod_subdomain}"
  value    = each.value
  type     = "A"
  proxied  = false
  ttl      = 60
  comment  = "Active-active k3s API VIP (Contabo additional IP, kept live by keepalived). DNS-only round-robin: raw TLS on 6443."
}
