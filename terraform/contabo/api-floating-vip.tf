# ---------------------------------------------------------------------------
# HA for the EXTERNAL k3s API endpoint — the Contabo floating-VIP half.
#
# etcd is HA (3 members) but the external API endpoint is not: 6443 is opened on
# the public interface of only the primary control plane (provisioning.tf), so a
# loss of that node makes the healthy control plane externally unreachable to
# kubectl / GitHub Actions CD / every consumer's cluster-query.yml.
#
# The fix is a Contabo "additional IP" used as a floating public VIP. keepalived
# on the three durable control planes (helm/fuzeinfra/templates/api-vip-keepalived.yaml)
# elects a MASTER over the private VLAN and reassigns this IP to it via the Contabo
# API on failover; external clients keep a normal kubeconfig pointed at the VIP.
# The Cloudflare-tunnel break-glass path (cloudflare.tf, k8s-api.<prod domain>) is
# the second, independent route for when the Contabo IP/network itself is the fault.
#
# WHAT TERRAFORM DOES HERE, AND DELIBERATELY DOES NOT.
#   - It publishes a DNS-only A record for the VIP and adds the VIP to each
#     apiserver's tls-san (control-planes.tf), so kubectl validates against it.
#   - It does NOT order the additional IP. Ordering is a purchase, and a bare
#     `terraform apply` against Contabo must not silently buy an IP (and would
#     collide with the add-on 1501 drift caution in modules/contabo-k3s-node).
#     Order it (panel or API), then set api_vip_address to the assigned value.
#
# GATED: every resource here is empty/absent until api_vip_enabled = true AND
# api_vip_address is set, so merging with the defaults is a no-op.
# See docs/runbooks/api-floating-vip.md.
# ---------------------------------------------------------------------------

variable "api_vip_enabled" {
  description = "Publish the floating-VIP DNS record and add the VIP to each apiserver tls-san. Order the additional IP first and set api_vip_address."
  type        = bool
  default     = false
}

variable "api_vip_address" {
  description = "The ordered Contabo additional IP used as the floating API VIP (e.g. 203.0.113.10). Empty leaves everything inert."
  type        = string
  default     = ""
}

variable "api_vip_hostname_label" {
  description = "Label (relative to the prod subdomain) for the VIP DNS record. Full host = <label>.<prod_subdomain>.<zone_name>."
  type        = string
  default     = "api"
}

locals {
  api_vip_active = local.cloudflare_enabled && var.api_vip_enabled && var.api_vip_address != ""
  api_vip_host   = "${var.api_vip_hostname_label}.${local.prod_domain}"
}

# DNS: api.prod.fuzefront.com -> the floating VIP, DNS-ONLY (grey cloud).
#
# NOT proxied: the k8s API is raw TLS on 6443 with client-cert/token auth that
# Cloudflare's HTTP proxy cannot pass. A DNS-only A record is a plain hostname for
# VIP:6443. It is more specific than the *.prod and * proxied wildcards, so it
# resolves straight to the VIP and is unaffected by them.
resource "cloudflare_record" "api_vip" {
  count   = local.api_vip_active ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "${var.api_vip_hostname_label}.${var.prod_subdomain}"
  value   = var.api_vip_address
  type    = "A"
  proxied = false
  ttl     = 60
  comment = "Floating k3s API VIP (Contabo additional IP, moved by keepalived). DNS-only: raw TLS on 6443."
}
