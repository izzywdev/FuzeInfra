# ---------------------------------------------------------------------------
# Durable storage nodes — Longhorn replica hosts with ≥300 GiB root disk
#
# PURPOSE
# -------
# The three existing durable nodes (fuze-core-1/2/3) share Longhorn replica
# placement. As of 2026-10-13, fuze-core-3 has 154.9 GiB raw disk with
# 201 GiB Longhorn-scheduled (139% overcommit). When those nodes undergo
# write spikes, Longhorn block engines can freeze in-flight I/O.
#
# This module provisions dedicated storage nodes that carry NO control-plane
# etcd or non-storage workloads — only Longhorn replicas and stateful pods
# that tolerate the durable taint. Adding one node with ≥300 GiB disk adds
# ~210 GiB of usable Longhorn capacity (300 × 70% after Longhorn's 30%
# reserve), redistributing scheduled replicas across a wider pool.
#
# Existing replicas do NOT auto-migrate unless Longhorn replica auto-balance
# is enabled. Set it after the node joins:
#   kubectl -n longhorn-system patch settings.longhorn.io \
#     replica-auto-balance --type=merge -p '{"value":"least-effort"}'
# See the comment in argocd/applications/longhorn.yaml defaultSettings.
#
# WHY NOT EXPANDING AN EXISTING NODE'S DISK
# ------------------------------------------
# The Contabo Terraform provider has no additional-block-volume resource.
# Root-disk resizes require instance reinstall. Adding a new VPS is the only
# Terraform-idiomatic path to more raw disk in Longhorn's pool.
#
# PROVISIONING CHECKLIST (run once)
# -----------------------------------
# 1. Set durable_node_product_id in terraform.tfvars to a Contabo SKU with
#    ≥300 GiB root disk. Verify via:
#      GET https://api.contabo.com/v1/compute/instances/product-pricing
#    or check https://contabo.com/en/cloud-vps/ for current SKUs.
#    Suggestion: Cloud VPS 3 / Cloud VPS 4 family in the EU region.
#
# 2. Set durable_node_count = 1 in terraform.tfvars (or TF_VAR_durable_node_count).
#
# 3. Preview: terraform plan -target=module.durable_storage_nodes
#
# 4. Apply: terraform apply -target=module.durable_storage_nodes
#
# 5. Order the private-networking add-on for the new instance:
#    Run the ca-private-net workflow:
#      action=upgrade instance_id=<new-contabo-id>
#      action=assign   instance_id=<new-contabo-id>
#    The k3s join unit re-runs on the next boot once the VLAN is up.
#    Monitor the node: kubectl get nodes -w
#
# 6. Once the node is Ready with the longhorn label, Longhorn will begin
#    placing NEW replica shards on it. To rebalance existing ones away from
#    fuze-core-3, run the replica-auto-balance patch above and monitor the
#    Longhorn UI until fuze-core-3's scheduled disk usage drops below 100%.
#
# 7. After confirming stable Longhorn scheduling, consider lowering
#    storageOverProvisioningPercentage back to 100% to prevent silent future
#    overcommit.
# ---------------------------------------------------------------------------

module "durable_storage_nodes" {
  source = "../../modules/contabo-k3s-node"

  requests = [
    for i in range(var.durable_node_count) : {
      name       = "fuze-durable-storage-${i + 1}"
      product_id = var.durable_node_product_id
      region     = var.durable_node_region
      role       = "durable"
      labels = {
        # Required by Longhorn's createDefaultDiskLabeledNodes=true setting:
        # only nodes with this label get a Longhorn replica disk.
        "node.longhorn.io/create-default-disk" = "true"
        # Matches the nodeSelector used by durable-pinned stateful workloads
        # in helm/fuzeinfra/values-contabo.yaml and the Longhorn taintToleration.
        "fuzeinfra.io/durable" = "true"
      }
    }
  ]

  contabo_client_id     = var.contabo_client_id
  contabo_client_secret = var.contabo_client_secret
  contabo_api_user      = var.contabo_api_user
  contabo_api_password  = var.contabo_api_password

  k3s_server_url = "https://${local.server_ip}:6443"
  k3s_node_token = var.k3s_node_token
  k3s_channel    = var.k3s_channel

  image_id       = var.image_id
  ssh_public_key = var.ssh_public_key

  # Longhorn attaches volumes over iSCSI and serves RWX over NFS — every
  # replica-hosting node needs open-iscsi, nfs-common and /var/lib/longhorn.
  enable_longhorn_prereqs = true

  # Place on VLAN 60932 (same as all other durable nodes). Membership +
  # the paid per-instance add-on are ordered out-of-band via ca-private-net
  # (action=upgrade then action=assign).
  private_network_id = var.node_private_network_id
}
