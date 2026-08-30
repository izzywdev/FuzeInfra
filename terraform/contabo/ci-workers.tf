# ---------------------------------------------------------------------------
# CI runner pool — dedicated node(s) tainted fuzeinfra.io/ci=true:NoSchedule
#
# Only ARC runner pods (with a matching toleration) schedule here.
# Prod/infra workloads never land on this node, keeping CI jobs isolated.
#
# This is FuzeInfra's OWN CI infrastructure (self-hosted GitHub ARC runners),
# NOT the cluster-autoscaler baseline and NOT consumer capacity — so it
# legitimately lives in FuzeInfra's Terraform. It is invisible to the
# autoscaler: CI nodes are never tagged `fuzeinfra-elastic`, so the
# externalgrpc provider classifies them as foreign (never a scale-down
# candidate). See docs/adr/0001-cluster-autoscaling-identity-scoped-baseline.md.
#
# To provision:  set ci_worker_count = N in terraform.tfvars (or TF_VAR_ci_worker_count
#                 in CI, see .github/workflows/terraform-plan-apply.yml) and apply.
#                 Nodes are named fuzeinfra-ci-runner-1..N. Currently 2 (FuzeInfra#586:
#                 fuzeinfra-ci-runner-1 alone was saturated by ~22 ARC scale sets).
# To tear down:  lower the count and apply (removes the highest-numbered node(s) first,
#                since `requests` is keyed by name — see modules/contabo-k3s-node/main.tf).
# ---------------------------------------------------------------------------
module "ci_workers" {
  source = "../../modules/contabo-k3s-node"

  requests = [
    for i in range(var.ci_worker_count) : {
      name = "fuzeinfra-ci-runner-${i + 1}"
      # Per-index product so growing the pool never disturbs an existing node.
      # fuzeinfra-ci-runner-1 (index 0) was created on var.product_id (the shared
      # CONTABO_PRODUCT_ID, currently the retired "V92" SKU). Contabo cannot
      # repackage a live VPS, so product_id is effectively ForceNew — feeding it a
      # new value would destroy+recreate the running node (and V92 can no longer be
      # ordered: "No offer was found for product ID V92"). So index 0 KEEPS
      # var.product_id (matches its live state → no diff), and every ADDITIONAL node
      # uses var.ci_worker_product_id (the current catalog SKU, "V153" = Cloud VPS 4,
      # 4 vCPU/8 GiB — same size). Result: a mixed pool (node-1 V92, node-2 V153) with
      # zero disruption. Migrate node-1 onto the current SKU later in a window, by
      # bumping CONTABO_PRODUCT_ID (its replacement is then a deliberate, drained op).
      product_id = i == 0 ? var.product_id : (var.ci_worker_product_id != "" ? var.ci_worker_product_id : var.product_id)
      region     = var.ci_worker_region
      role       = "ci"
      labels     = {}
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
}
