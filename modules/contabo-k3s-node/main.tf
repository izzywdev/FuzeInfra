locals {
  # Key the instances by request name so adding/removing a request only touches
  # that node (a positional list would force-recreate everything after an insert).
  requests = { for r in var.requests : r.name => r }

  # Private networking is on when the caller either asks this module to CREATE a
  # network (private_network_name) or points at an EXISTING one to attach to
  # (private_network_id). Only the former creates a contabo_private_network;
  # membership for the latter is ordered out-of-band by the ca-private-net
  # workflow, so Terraform can never detach a member it did not author.
  private_network_enabled = var.private_network_name != "" || var.private_network_id > 0
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
    private_network_enabled = local.private_network_enabled
    private_iface           = var.private_iface
    # node-role=<role> first (the contract label), then any extra labels.
    node_labels = join(" ", concat(
      ["--node-label node-role=${each.value.role}"],
      ["--node-label fuzeinfra.io/role=${each.value.role}"],
      [for k, v in each.value.labels : "--node-label ${k}=${v}"],
    ))
  })

  # PRIVATE NETWORKING ADD-ON. Contabo's per-instance Private Networking add-on
  # is a PAID capability that must be ORDERED at create time (or via a
  # separate upgrade call for an existing instance -- see
  # docs/design/off-vlan-node-failure-policy.md section 2a). Without it, a
  # later attach to the private network returns HTTP 402 regardless of
  # anything else being correct.
  #
  # This was the exact gap that stranded fuzeinfra-ci-runner-2 off-VLAN: this
  # module already wrote a working eth1 netplan config into cloud-init purely
  # from private_network_enabled, with no corresponding purchase -- so a node
  # could come up believing it has a private NIC and never actually get one.
  # Ordering it HERE, gated on the identical condition that drives the
  # cloud-init eth1 config, makes "configures eth1" and "paid for eth1" a
  # single atomic decision instead of two things that can silently drift.
  #
  # Add-on id 1477 confirmed against the live API on 2026-09-03 while ordering
  # it for fuzeinfra-ci-runner-2 (POST /v1/compute/instances/{id}/upgrade
  # {"privateNetworking":{}} -> HTTP 200, {"addonsIds":[1477]}). The provider
  # schema types `id` as a string.
  dynamic "add_ons" {
    for_each = local.private_network_enabled ? [1] : []
    content {
      id       = "1477"
      quantity = 1
    }
  }

  lifecycle {
    # cloud-init only runs on first boot, so re-rendering user_data (e.g. a
    # whitespace change) must not trigger a destroy/recreate of a live node.
    # add_ons is deliberately NOT in this list: unlike user_data, a change to
    # whether the add-on is ordered is not something that should be silently
    # absorbed -- it is a real cost/capability change and should plan visibly.
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
