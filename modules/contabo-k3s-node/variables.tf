# ---------------------------------------------------------------------------
# Node requests
#
# A consumer repo declares one or more nodes it needs in
# deploy/terraform/node-request.tf as a list of objects. The handler passes that
# list straight through to this module. Keep this object shape stable — it is the
# public contract consumers code against.
# ---------------------------------------------------------------------------
variable "requests" {
  description = "List of node requests to reconcile. Each becomes one Contabo VPS joined to the cluster as a k3s agent."
  type = list(object({
    name       = string                       # stable node name (also the Contabo display name + k3s --node-name)
    product_id = string                       # Contabo product/plan ID (e.g. V45 / VPS tier). Validated by the handler whitelist.
    region     = optional(string, "EU")       # Contabo region. Whitelist restricts auto-apply to EU.
    role       = optional(string, "workload") # node role → applied as the `node-role=<role>` label
    labels     = optional(map(string), {})    # extra k3s node labels (key=value)
  }))

  validation {
    condition     = length(var.requests) == length(distinct([for r in var.requests : r.name]))
    error_message = "Each request.name must be unique — it keys the instance and the k3s node name."
  }
}

# ---------------------------------------------------------------------------
# Contabo API credentials — supplied by the handler from FuzeInfra CI secrets.
# Never hardcode these; never commit them to a consumer repo.
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
  sensitive   = true
}

variable "contabo_api_password" {
  description = "Contabo account password"
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# k3s join parameters — supplied by the handler from FuzeInfra CI secrets.
# The new VPS runs `k3s agent` against this server URL using this token.
# ---------------------------------------------------------------------------
variable "k3s_server_url" {
  description = "URL of the existing k3s server to join, e.g. https://<server-ip>:6443"
  type        = string
}

variable "k3s_node_token" {
  description = "k3s node-token from the server (/var/lib/rancher/k3s/server/node-token)"
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# VPS provisioning
# ---------------------------------------------------------------------------
variable "image_id" {
  description = "Contabo OS image UUID for the node (Ubuntu 24.04 LTS recommended)"
  type        = string
}

variable "ssh_public_key" {
  description = "SSH public key injected into the VPS via cloud-init for break-glass access"
  type        = string
}

variable "k3s_channel" {
  description = "k3s release channel or version pin passed to the installer (INSTALL_K3S_CHANNEL). Pin to match the server's k3s version. Default v1.36 matches current cluster (FuzeInfra#318)."
  type        = string
  default     = "v1.36"
}

# ---------------------------------------------------------------------------
# Private network attach (optional)
#
# When private_network_name is non-empty a contabo_private_network is created
# (or reused by name) and every node in this request set is attached to it, so
# node-to-node k3s/overlay traffic stays off the public internet.
# ---------------------------------------------------------------------------
variable "private_network_name" {
  description = "Name of the Contabo private network to attach nodes to. Empty string disables private networking."
  type        = string
  default     = ""
}

variable "private_network_region" {
  description = "Region for the private network (must match the nodes' region)."
  type        = string
  default     = "EU"
}
