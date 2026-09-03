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

# ---------------------------------------------------------------------------
# Longhorn node prerequisites (optional, default OFF)
#
# Longhorn attaches volumes over iSCSI and serves RWX over NFS, so every node
# that will host a Longhorn replica or a Longhorn-backed pod needs open-iscsi
# (+ iscsid running), nfs-common, and a /var/lib/longhorn data dir. When true,
# cloud-init installs these on first boot. Default false keeps a merge a no-op
# for existing consumers. See docs/design/longhorn-storage.md §3.
# ---------------------------------------------------------------------------
variable "enable_longhorn_prereqs" {
  description = "Install Longhorn node prerequisites (open-iscsi, nfs-common, /var/lib/longhorn) via cloud-init on first boot."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Attach to an EXISTING private network (the prod mode)
#
# private_network_name (above) CREATES/manages a contabo_private_network and
# reconciles its instance_ids to exactly this request set. Pointing that at the
# live prod network 60932 would try to DETACH every member this module did not
# author -- the control planes and every elastic node. See the ELASTIC-EXCLUSION
# note in terraform/contabo/private-network.tf.
#
# private_network_id is the safe alternative: it enables the private-networking
# half of cloud-init (eth1 netplan, --node-ip/--flannel-iface, the VLAN firewall
# rule, the off-VLAN quarantine) WITHOUT declaring any network resource, so
# Terraform never touches membership. Membership + the paid per-instance add-on
# are ordered out-of-band, idempotently, by the `ca-private-net` workflow
# (action=upgrade then action=assign, or action=enroll-elastics).
#
# The order does not matter: the assign may land minutes after boot, and it may
# reboot the node. The join is a systemd oneshot that re-runs every boot until
# it succeeds on the VLAN, so a late assign repairs the node by itself.
# ---------------------------------------------------------------------------
variable "private_network_id" {
  description = "Contabo private-network id to place nodes on. Enables the private-networking half of cloud-init without Terraform managing network membership. 0 disables. Mutually exclusive with private_network_name."
  type        = number
  # DEFAULT ON (60932 = the live prod VLAN). This module exists only to join
  # FuzeInfra prod k3s and prod is VLAN-only, so off-VLAN is never the correct
  # answer -- it must not be what a caller gets by saying nothing. That silence
  # is exactly how fuzeinfra-ci-runner-2 was born off-VLAN two days after the
  # cutover: ci-workers.tf simply never mentioned private networking.
  # Secure-by-default also covers callers that do not exist yet, which a
  # per-caller fix cannot. Set 0 to opt out deliberately.
  default = 60932

  validation {
    condition     = var.private_network_id == 0 || var.private_network_name == ""
    error_message = "Set either private_network_id (attach to an existing network, membership managed out-of-band) or private_network_name (this module creates and reconciles the network) -- never both."
  }
}

variable "private_iface" {
  description = "Private NIC device name the Contabo VPC attaches as (eth1 on a 2-NIC Ubuntu 24.04 image). When private_network_name is set, the node brings this interface up via netplan and k3s routes its overlay (--flannel-iface) + node-ip over it. NOTE: the per-instance Contabo VPC add-on is a MANUAL panel purchase (HTTP 402 otherwise) that Terraform cannot order."
  type        = string
  default     = "eth1"
}
