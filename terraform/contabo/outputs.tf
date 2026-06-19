output "server_ip" {
  description = "Public IP of the Contabo VPS"
  value       = local.server_ip
}

output "domain" {
  description = "Base domain used for Ingress hostnames"
  value       = local.domain
}

output "argocd_url" {
  description = "ArgoCD UI (expose via port-forward: kubectl -n argocd port-forward svc/argocd-server 8080:443)"
  value       = "https://${local.server_ip}:8080 (via port-forward)"
}

output "argocd_initial_password_command" {
  description = "Command to retrieve the initial ArgoCD admin password"
  value       = "ssh root@${local.server_ip} \"kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d\""
}

output "kubeconfig_path" {
  description = "Local path to the extracted kubeconfig"
  value       = "${path.root}/k3s-kubeconfig.yaml"
}

output "cloudflare_tunnel_id" {
  description = "Cloudflare Named Tunnel ID (empty when cloudflare_api_token not set)"
  value       = local.cloudflare_enabled ? cloudflare_tunnel.fuzeinfra[0].id : ""
}

output "prod_domain" {
  description = "Public domain pointing to this cluster via Cloudflare tunnel"
  value       = local.cloudflare_enabled ? local.prod_domain : ""
}

output "argocd_url_public" {
  description = "ArgoCD public URL (available after Cloudflare tunnel is wired)"
  value       = local.cloudflare_enabled ? "https://argocd.${local.prod_domain}" : "https://${local.server_ip}:8080 (via port-forward)"
}
