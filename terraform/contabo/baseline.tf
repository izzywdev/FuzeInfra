# ---------------------------------------------------------------------------
# Baseline worker pool — TF-managed, fixed-size, NEVER touched by the cluster
# autoscaler.
#
# Cluster topology (see docs/superpowers/specs/
# 2026-07-03-cluster-node-autoscaling-design.md, section 5 "Terraform / CA
# boundary"):
#
#   | Baseline pool (this file + vps.tf)      | Elastic pool                |
#   |------------------------------------------|------------------------------|
#   | Owner: Terraform (declarative, gated)     | Owner: Cluster Autoscaler    |
#   | Size: fixed — 1 control-plane (vps.tf)    | min=0 .. max=2, live-scaled  |
#   |       + var.baseline_worker_count workers |                              |
#   |       (default 2) = 3 nodes total         |                              |
#   | Identity: untagged / role=baseline         | tag `fuzeinfra-elastic`      |
#   | In CA's node-group config?  NO             | YES — the only group CA owns|
#
# ELASTIC-EXCLUSION INVARIANT (do not weaken without updating Task 16's CI
# drift guard too):
#   Terraform in this directory NEVER enumerates Contabo's account-wide
#   instance list — there is no `data "contabo_instance"` / for_each over "all
#   instances" anywhere in terraform/contabo or modules/contabo-k3s-node. Every
#   resource here is scoped to an explicit, named request (the control-plane
#   VPS in vps.tf, and the fixed `baseline-worker-N` set below). The autoscaler
#   creates/destroys its own `fuzeinfra-elastic`-tagged Contabo instances
#   out-of-band via the externalgrpc provider (cluster-autoscaler/
#   contabo-externalgrpc), completely outside this Terraform state. Because
#   nothing here ever queries "give me every instance", `terraform apply` can
#   never adopt, plan a diff against, or destroy an elastic node — there is no
#   mechanism by which it could. Baseline nodes provisioned by this module
#   never set `tags` (see modules/contabo-k3s-node/main.tf), so there's no risk
#   of a baseline node colliding with the `fuzeinfra-elastic` tag either.
#   If a future change introduces any broad `data` source or for_each over
#   Contabo's instance inventory, it MUST filter out instances tagged
#   `fuzeinfra-elastic` before this module/state can safely touch them.
# ---------------------------------------------------------------------------

module "baseline_workers" {
  # NOTE: this module declares its own `provider "contabo" {}` block (a
  # "legacy" module in Terraform's terms), so it cannot be used with `count`,
  # `for_each`, or `depends_on` at the call site. Scaling to zero nodes is
  # expressed instead by passing an empty `requests` list — the module already
  # supports that (its `for_each` is keyed on `local.requests`, which is `{}`
  # for an empty list), so `baseline_worker_count = 0` is still valid.
  source = "../../modules/contabo-k3s-node"

  requests = [
    for i in range(var.baseline_worker_count) : {
      name       = "fuzeinfra-baseline-worker-${i + 1}"
      product_id = var.baseline_worker_product_id != "" ? var.baseline_worker_product_id : var.product_id
      region     = var.baseline_worker_region
      role       = "baseline"
      labels     = {}
    }
  ]

  # Credentials + join params reused verbatim from this directory's existing
  # vars/locals — no new secret handling introduced.
  contabo_client_id     = var.contabo_client_id
  contabo_client_secret = var.contabo_client_secret
  contabo_api_user      = var.contabo_api_user
  contabo_api_password  = var.contabo_api_password

  k3s_server_url = "https://${local.server_ip}:6443"
  k3s_node_token = var.k3s_node_token
  k3s_channel    = var.k3s_channel

  image_id       = var.image_id
  ssh_public_key = var.ssh_public_key

  # No private network: baseline workers join over the same public-IP overlay
  # as the control-plane (VXLAN today, WireGuard after the provisioning.tf
  # cutover — see that file's comment). Nothing here to change when that
  # cutover happens; the module's cloud-init only opens the agent-side ports.
}

# ---------------------------------------------------------------------------
# CI runner pool — dedicated node(s) tainted fuzeinfra.io/ci=true:NoSchedule
#
# Only ARC runner pods (with a matching toleration) schedule here.
# Prod/infra workloads never land on this node, keeping CI jobs isolated.
#
# To provision:  set ci_worker_count = 1 in terraform.tfvars and apply.
# To tear down:  set ci_worker_count = 0 and apply.
# ---------------------------------------------------------------------------
module "ci_workers" {
  source = "../../modules/contabo-k3s-node"

  requests = [
    for i in range(var.ci_worker_count) : {
      name       = "fuzeinfra-ci-runner-${i + 1}"
      product_id = var.ci_worker_product_id != "" ? var.ci_worker_product_id : var.product_id
      region     = var.baseline_worker_region
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
