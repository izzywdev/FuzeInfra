# ---------------------------------------------------------------------------
# Contabo API credentials (FuzeInfra GitHub Actions secrets: CONTABO_*)
# ---------------------------------------------------------------------------
variable "contabo_client_id" {
  description = "Contabo OAuth2 client ID"
  type        = string
  sensitive   = true
}

variable "contabo_client_secret" {
  description = "Contabo OAuth2 client secret"
  type        = string
  sensitive   = true
}

variable "contabo_api_user" {
  description = "Contabo account email address"
  type        = string
}

variable "contabo_api_password" {
  description = "Contabo account password"
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# VPS configuration
# ---------------------------------------------------------------------------
variable "instance_display_name" {
  description = "Display name for the VPS in the Contabo dashboard"
  type        = string
  default     = "mendys-prod"
}

variable "image_id" {
  description = "Contabo OS image UUID (e.g. Ubuntu 24.04 LTS)"
  type        = string
}

variable "product_id" {
  description = "Contabo product/plan UUID (e.g. VPS S = 4 vCPU / 8 GB)"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key injected into the VPS via cloud-init"
  type        = string
}

variable "ssh_private_key_path" {
  description = "Path to the SSH private key matching ssh_public_key (used for remote-exec provisioning)"
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "server_user" {
  description = "SSH user on the VPS"
  type        = string
  default     = "root"
}

# ---------------------------------------------------------------------------
# TLS — Let's Encrypt (cert-manager HTTP-01)
#
# No hardcoded default: the operator MUST supply a real address (via tfvars or
# TF_VAR_letsencrypt_email). The validation block rejects an empty value so a
# missing email fails fast at plan time rather than producing a broken ACME
# ClusterIssuer that silently never issues certs.
# ---------------------------------------------------------------------------
variable "letsencrypt_email" {
  description = "Email for the Let's Encrypt ACME account (expiry notices). REQUIRED — no default."
  type        = string

  validation {
    condition     = length(trimspace(var.letsencrypt_email)) > 0
    error_message = "letsencrypt_email must be a non-empty email address (set via tfvars or TF_VAR_letsencrypt_email)."
  }
}

# ---------------------------------------------------------------------------
# DNS — Cloudflare (DNS-only; proxied = false on every record)
# ---------------------------------------------------------------------------
variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:DNS:Edit on the mendysrobotics.com zone"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID for mendysrobotics.com (FuzeInfra secret: CLOUDFLARE_ZONE_ID_MENDYS)"
  type        = string
}

variable "domain" {
  description = "Root domain managed in Cloudflare for Mendys"
  type        = string
  default     = "mendysrobotics.com"
}

# Subdomains that resolve to this VPS. apex/www are intentionally EXCLUDED —
# those are owned by landing/infra/terraform in the MendysRobotics repo and must
# never be touched from here.
variable "subdomains" {
  description = "Subdomains (A-records) pointing at this cluster's ingress"
  type        = list(string)
  default = [
    "live",
    "api.live",
    "marketplace",
    "wp",
    "argocd",
  ]
}

# ---------------------------------------------------------------------------
# GitHub (for storing MENDYS_KUBECONFIG — matches FuzeInfra terraform/contabo/variables.tf)
# ---------------------------------------------------------------------------
variable "github_token" {
  description = "GitHub PAT with repo + secrets write permission (GH_TF_TOKEN secret)"
  type        = string
  sensitive   = true
}

variable "github_owner" {
  description = "GitHub owner (org or user)"
  type        = string
  default     = "izzywdev"
}

variable "github_repo" {
  description = "GitHub repository where MENDYS_KUBECONFIG secret is stored"
  type        = string
  default     = "FuzeInfra"
}

# ---------------------------------------------------------------------------
# GitOps source (MendysRobotics provides declarations; FuzeInfra executes)
# ---------------------------------------------------------------------------
variable "mendys_repo_url" {
  description = "Git repo ArgoCD pulls Mendys manifests from"
  type        = string
  default     = "https://github.com/izzywdev/MendysRobotics.git"
}

variable "mendys_repo_revision" {
  description = "Git revision ArgoCD tracks for Mendys manifests"
  type        = string
  default     = "main"
}
