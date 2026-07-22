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

# ---------------------------------------------------------------------------
# Cloudflare Zero Trust (optional — leave api_token empty to skip)
#
# When cloudflare_api_token is set, a single `terraform apply` in this
# directory creates the Named Tunnel, DNS records, and Access policies
# AND injects the computed token into the cluster — no manual steps.
#
# Obtain a Cloudflare API token with these permissions:
#   Zone > DNS > Edit
#   Account > Cloudflare Tunnel > Edit
#   Account > Access: Apps and Policies > Edit
# Create at: https://dash.cloudflare.com/profile/api-tokens
# ---------------------------------------------------------------------------
variable "cloudflare_api_token" {
  description = "Cloudflare API token. Leave empty to skip all Cloudflare resources."
  type        = string
  default     = ""
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (from https://dash.cloudflare.com → account settings)"
  type        = string
  default     = ""
}

variable "cloudflare_zone_id" {
  description = "Cloudflare Zone ID for the domain (from the zone overview page)"
  type        = string
  default     = ""
}

variable "tunnel_name" {
  description = "Name of the Named Tunnel in Cloudflare Zero Trust dashboard"
  type        = string
  # MUST match the deployed tunnel name. The production tunnel is named
  # "FuzeInfra" (confirmed in state); a mismatch makes terraform rename — and if
  # `name` is ForceNew, recreate — the tunnel, which would break all prod access.
  default = "FuzeInfra"
}

variable "prod_subdomain" {
  description = "Subdomain under zone_name that points to this cluster (e.g. 'prod' → prod.fuzefront.com)"
  type        = string
  default     = "prod"
}

variable "zone_name" {
  description = "Root DNS zone managed in Cloudflare (e.g. fuzefront.com)"
  type        = string
  default     = "fuzefront.com"
}

variable "allowed_admin_emails" {
  description = "Email addresses allowed through Cloudflare Access (receives email OTP)"
  type        = list(string)
  default     = ["izzy.weinberg@gmail.com"]
}

variable "access_session_duration" {
  description = "How long a Cloudflare Access session lasts before re-auth is required"
  type        = string
  default     = "24h"
}

variable "crit_bridge_token" {
  description = "Shared secret between Grafana and the crit-alert CF Worker (BRIDGE_TOKEN). Set in terraform.tfvars. Also injected into fuzeinfra-secrets as CRIT_BRIDGE_TOKEN so Grafana can read it."
  type        = string
  default     = ""
  sensitive   = true
}

variable "handoff_mcp_access_enabled" {
  description = "Create the more-specific CF Access 'bypass' app for mcp-handoff.<domain> so Anthropic Managed Agents (machine, non-interactive) skip the *.prod email-OTP wildcard; the handoff MCP server enforces its own HANDOFF_MCP_TOKEN bearer. Flip to true when the handoff MCP is deployed."
  type        = bool
  default     = false
}

variable "ci_worker_count" {
  description = "Number of TF-managed CI runner nodes to provision. DEFAULT 0; CI env sets TF_VAR_ci_worker_count=1 to spin up one dedicated CI node. CI nodes are tainted fuzeinfra.io/ci=true:NoSchedule so only ARC runner pods land there."
  type        = number
  default     = 0

  validation {
    condition     = var.ci_worker_count >= 0
    error_message = "ci_worker_count must be >= 0."
  }
}

variable "ci_worker_product_id" {
  description = "Contabo product/plan UUID for CI runner nodes. Defaults to the same plan as the control-plane (var.product_id) unless overridden. The cheapest VPS S tier is sufficient for most CI workloads."
  type        = string
  default     = ""
}

variable "ci_worker_region" {
  description = "Contabo region for CI runner nodes."
  type        = string
  default     = "EU"
}

variable "k3s_node_token" {
  description = "k3s node-token from the running server (/var/lib/rancher/k3s/server/node-token), used to join baseline worker nodes as k3s agents. Same secret already used by the infra-request-handler workflow (K3S_NODE_TOKEN) and modules/contabo-k3s-node — sourced from CI secrets / terraform.tfvars, never hardcoded."
  type        = string
  sensitive   = true
  default     = ""
}

variable "k3s_channel" {
  description = "k3s release channel/version pin for baseline worker nodes (INSTALL_K3S_CHANNEL). Pinned to v1.36 to match the running control-plane and prevent skew (FuzeInfra#318)."
  type        = string
  default     = "v1.36"
}

variable "enable_argocd_provisioner" {
  description = <<-EOT
    Run null_resource.argocd_sync, which SSHes to the server (using
    ssh_private_key_path) to re-apply the ArgoCD Application/Project/SealedSecrets
    manifests. OFF by default: it requires a local SSH private key FILE that does
    not exist on CI runners (the merge-to-apply CD), so it breaks CI applies.
    Ongoing reconciliation is handled by ArgoCD selfHeal, and one-time argo
    registration by the argocd-register.yml workflow. Enable locally (in
    terraform.tfvars) only if you want terraform to push argo manifests via SSH.
  EOT
  type        = bool
  default     = false
}

