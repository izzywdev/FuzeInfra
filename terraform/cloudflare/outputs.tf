output "tunnel_id" {
  description = "Cloudflare Named Tunnel ID"
  value       = cloudflare_tunnel.fuzeinfra.id
}

output "tunnel_cname" {
  description = "CNAME value for DNS records pointing to this tunnel"
  value       = cloudflare_tunnel.fuzeinfra.cname
}

output "tunnel_token" {
  description = <<-EOT
    Token for cloudflared (--token flag).
    Store in the cluster with:
      kubectl patch secret -n fuzeinfra fuzeinfra-secrets \
        -p "{\"data\":{\"CLOUDFLARE_TUNNEL_TOKEN\":\"$(terraform output -raw tunnel_token | base64 -w0)\"}}"
    Or run: scripts/setup-cloudflare-tunnel.sh
  EOT
  value     = local.tunnel_token
  sensitive = true
}

output "prod_domain" {
  description = "The prod subdomain (e.g. prod.fuzefront.com)"
  value       = local.prod_domain
}

output "access_application_id" {
  description = "Cloudflare Access Application ID for the admin services wildcard"
  value       = cloudflare_access_application.admin_services.id
}
