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
  value       = local.cloudflare_enabled ? cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id : ""
  sensitive   = true
}

output "prod_domain" {
  description = "Public domain pointing to this cluster via Cloudflare tunnel"
  # Derived only from non-sensitive vars so it's always visible in plan output.
  value = "${var.prod_subdomain}.${var.zone_name}"
}

output "argocd_url_public" {
  description = "ArgoCD public URL (public once Cloudflare tunnel is wired)"
  value       = "https://argocd.${var.prod_subdomain}.${var.zone_name}"
}

# ---------------------------------------------------------------------------
# Contabo Object Storage (S3) — see object-storage.tf.
# Endpoint/region are read from state, never hardcoded. Empty strings/[] when
# var.enable_object_storage is false.
# ---------------------------------------------------------------------------
output "object_storage_id" {
  description = "Contabo Object Storage tenant ID (empty when object storage is disabled)."
  value       = var.enable_object_storage ? contabo_object_storage.this[0].id : ""
}

output "object_storage_region" {
  description = "Object Storage region (empty when disabled)."
  value       = var.enable_object_storage ? var.object_storage_region : ""
}

output "object_storage_s3_url" {
  description = "Full S3 endpoint incl. scheme (e.g. https://eu2.contabostorage.com). Strip the scheme to get the bare host the Loki chart's loki.s3.endpoint wants. Empty when disabled."
  value       = var.enable_object_storage ? contabo_object_storage.this[0].s3_url : ""
}

output "object_storage_s3_tenant_id" {
  description = "S3 tenant ID (path-style bucket prefix / canonical tenant). Empty when disabled."
  value       = var.enable_object_storage ? contabo_object_storage.this[0].s3_tenant_id : ""
}

output "object_storage_buckets" {
  description = "Names of the provisioned buckets (empty list when disabled)."
  value = var.enable_object_storage ? [
    contabo_object_storage_bucket.loki[0].name,
    contabo_object_storage_bucket.backups[0].name,
    contabo_object_storage_bucket.blobs[0].name,
  ] : []
}
