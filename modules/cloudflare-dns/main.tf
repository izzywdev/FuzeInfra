# =============================================================================
# cloudflare-dns — generic product DNS for the shared Cloudflare Tunnel.
#
# A product on its OWN domain (a separate CF zone, e.g. mendysrobotics.com)
# declares its hosts here from its own deploy/terraform; FuzeInfra's
# infra-request-handler applies it (FuzeInfra holds the CF token + TF state, the
# consumer holds neither). Each host becomes a PROXIED CNAME to the shared tunnel
# entrypoint — CF terminates TLS at the edge and the tunnel's generic catch-all
# forwards to Traefik, which host-routes by Ingress.
#
# This module is consumer-agnostic: NO product hostnames are hardcoded. The
# caller supplies domain/zone_id/hosts. Apex/www are never managed here — pass
# only additive subdomain labels.
# =============================================================================

variable "domain" {
  description = "Zone apex domain, e.g. mendysrobotics.com (used to build FQDNs)"
  type        = string
}

variable "zone_id" {
  description = "Cloudflare zone ID for var.domain (a public identifier, not a secret)"
  type        = string
}

variable "tunnel_hostname" {
  description = "Public hostname fronting the shared FuzeInfra Cloudflare Tunnel; proxied CNAME target for every host"
  type        = string
  default     = "prod.fuzefront.com"
}

variable "hosts" {
  description = "Subdomain labels to create (each -> <label>.<domain> CNAME -> tunnel, proxied). Additive subdomains only."
  type        = list(string)
  default     = []
}

resource "cloudflare_record" "this" {
  for_each = toset(var.hosts)
  zone_id  = var.zone_id
  name     = "${each.value}.${var.domain}"
  value    = var.tunnel_hostname
  type     = "CNAME"
  proxied  = true
  ttl      = 1
}
