# ---------------------------------------------------------------------------
# Contabo API credentials
# Get these from: https://new.contabo.com/account/api-credentials
# ---------------------------------------------------------------------------
variable "contabo_client_id" {
  description = "Contabo OAuth2 client ID (from customer portal API credentials)"
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
# Find image/product IDs at: https://api.contabo.com/#tag/Images/operation/retrieveImage
# or run: curl -s -H "Authorization: Bearer <token>" https://api.contabo.com/v1/compute/images
# ---------------------------------------------------------------------------
variable "instance_display_name" {
  description = "Display name for the VPS in Contabo dashboard"
  type        = string
  default     = "fuzeinfra-prod"
}

variable "image_id" {
  description = "Contabo OS image UUID. Ubuntu 24.04 LTS image ID (find via Contabo API or dashboard)"
  type        = string
  # Example: look up with `contabo images list` or the Contabo API
}

variable "product_id" {
  description = "Contabo product/plan UUID (e.g. VPS S, M, L). Find in Contabo dashboard URLs or API."
  type        = string
  # Example VPS S has 4 vCPU / 8 GB RAM / 200 GB SSD
}

variable "ssh_public_key" {
  description = "SSH public key to inject into the VPS via cloud-init (contents of ~/.ssh/id_ed25519.pub)"
  type        = string
}

# ---------------------------------------------------------------------------
# SSH access (to provision k3s and ArgoCD)
# ---------------------------------------------------------------------------
variable "ssh_private_key_path" {
  description = "Path to the SSH private key matching the key registered in Contabo"
  type        = string
  default     = "~/.ssh/id_ed25519"
}

variable "server_user" {
  description = "SSH user on the VPS"
  type        = string
  default     = "root"
}

# ---------------------------------------------------------------------------
# Production domain
# Default uses nip.io (free wildcard DNS for any IP).
# Replace with your real domain if you have one pointed at the server.
# E.g. "infra.yourdomain.com" requires a wildcard *.infra.yourdomain.com DNS record.
# ---------------------------------------------------------------------------
variable "domain" {
  description = "Base domain for Ingress hostnames (e.g. grafana.<domain>)"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# GitHub (for setting the KUBE_CONFIG secret)
# ---------------------------------------------------------------------------
variable "github_token" {
  description = "GitHub personal access token with repo + secrets write permission"
  type        = string
  sensitive   = true
}

variable "github_owner" {
  description = "GitHub owner (org or user)"
  type        = string
  default     = "izzywdev"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "FuzeInfra"
}
