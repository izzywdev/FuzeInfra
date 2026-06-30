# ---------------------------------------------------------------------------
# Cloudflare DNS — DNS-only A-records for the Mendys subdomains.
#
# Every record is proxied = false (grey cloud) on purpose:
#   - Let's Encrypt HTTP-01 challenges must reach ingress-nginx directly.
#   - TLS is terminated by cert-manager in-cluster, NOT by Cloudflare.
#
# HARD CONSTRAINT: apex (mendysrobotics.com) and www are NOT managed here.
# They live in the MendysRobotics repo's landing/infra/terraform/dns.tf and
# must never be created/updated/destroyed from this module. Only the additive
# subdomain A-records below are owned here.
# ---------------------------------------------------------------------------
resource "cloudflare_record" "subdomains" {
  for_each = toset(var.subdomains)

  zone_id = var.cloudflare_zone_id
  name    = each.value
  type    = "A"
  content = local.server_ip
  ttl     = 300
  proxied = false

  comment = "Managed by FuzeInfra terraform/mendys-contabo → Mendys k3s ingress"
}
