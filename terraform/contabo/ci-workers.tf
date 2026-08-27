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
      name       = "fuzeinfra-ci-runner-${i + 1}"
      product_id = var.ci_worker_product_id != "" ? var.ci_worker_product_id : var.product_id
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
