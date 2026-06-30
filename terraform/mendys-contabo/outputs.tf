output "server_ip" {
  description = "Public IP of the Mendys Contabo VPS"
  value       = local.server_ip
}

output "domain" {
  description = "Root domain for Mendys ingress hostnames"
  value       = var.domain
}

output "subdomain_fqdns" {
  description = "FQDNs of the managed subdomain A-records"
  value       = [for s in var.subdomains : "${s}.${var.domain}"]
}

output "argocd_url" {
  description = "ArgoCD UI (once DNS + certs converge)"
  value       = "https://argocd.${var.domain}"
}

output "argocd_initial_password_command" {
  description = "Command to retrieve the initial ArgoCD admin password"
  value       = "ssh ${var.server_user}@${local.server_ip} \"kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d\""
}

output "kubeconfig_path" {
  description = "Local path to the extracted kubeconfig (base64 -w0 it → MENDYS_KUBECONFIG secret)"
  value       = "${path.root}/mendys-kubeconfig.yaml"
}
