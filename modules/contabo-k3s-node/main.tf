locals {
  # Key the instances by request name so adding/removing a request only touches
  # that node (a positional list would force-recreate everything after an insert).
  requests = { for r in var.requests : r.name => r }
}

# ---------------------------------------------------------------------------
# One VPS per request, each cloud-init'd to join the cluster as a k3s agent.
# ---------------------------------------------------------------------------
resource "contabo_instance" "node" {
  for_each = local.requests

  display_name = each.value.name
  image_id     = var.image_id
  product_id   = each.value.product_id
  region       = each.value.region

  # SSH key is injected via cloud-init (below) rather than ssh_keys, which takes
  # pre-registered numeric Contabo key IDs — matches the pattern in terraform/contabo.
  user_data = templatefile("${path.module}/cloud-init.tftpl", {
    ssh_public_key = var.ssh_public_key
    k3s_server_url = var.k3s_server_url
    k3s_node_token = var.k3s_node_token
    k3s_channel    = var.k3s_channel
    node_name      = each.value.name
    role           = each.value.role
    # Gated Longhorn node prereqs (open-iscsi/nfs-common//var/lib/longhorn).
    enable_longhorn_prereqs = var.enable_longhorn_prereqs
    # Private networking (Contabo VPC): bring up the private NIC + route k3s
    # over it only when the caller opted into a private network by name.
    private_network_enabled = var.private_network_name != ""
    private_iface           = var.private_iface
    # node-role=<role> first (the contract label), then any extra labels.
    node_labels = join(" ", concat(
      ["--node-label node-role=${each.value.role}"],
      ["--node-label fuzeinfra.io/role=${each.value.role}"],
      [for k, v in each.value.labels : "--node-label ${k}=${v}"],
    ))
  })

  lifecycle {
    # cloud-init only runs on first boot, so re-rendering user_data (e.g. a
    # whitespace change) must not trigger a destroy/recreate of a live node.
    ignore_changes = [user_data]
  }
}

# ---------------------------------------------------------------------------
# Optional private network — attaches every node in this request set so the
# k3s control/overlay traffic can stay off the public internet.
# ---------------------------------------------------------------------------
resource "contabo_private_network" "this" {
  count = var.private_network_name != "" ? 1 : 0

  name         = var.private_network_name
  region       = var.private_network_region
  description  = "FuzeInfra k3s node network (managed by contabo-k3s-node)"
  instance_ids = [for n in contabo_instance.node : n.id]
}
