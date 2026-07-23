# ---------------------------------------------------------------------------
# Autoscaling boundary — identity-scoped, floating baseline
# (see docs/superpowers/specs/2026-07-03-cluster-node-autoscaling-design.md,
# section 5, for the full rationale)
#
# Terraform in this directory manages EXACTLY ONE node: the control-plane VPS
# (contabo_instance.prod in vps.tf). It does not, and must never, enumerate or
# manage worker capacity — that is FuzeInfra staying decoupled from the
# consumer repos (FuzeFront, mendyrobotics, ...) that request worker nodes via
# their own repos' dispatch to infra-request-handler. Those consumer-dispatched
# nodes are provisioned out-of-band (NOT in this Terraform state) and are
# "foreign" to this directory — FuzeInfra stays unaware of why a consumer
# needed the node or what runs on it.
#
# The cluster autoscaler (Task 12+) owns a second, disjoint set of nodes:
# every instance it creates is tagged `fuzeinfra-elastic` and belongs to the
# "elastic" node group (min=0, max=2 — see helm/fuzeinfra/values-contabo.yaml).
# The Go provider (cluster-autoscaler/contabo-externalgrpc) classifies nodes by
# that tag via NodeGroupForNode — every other node (control-plane, consumer
# workers, anything dispatched in the future) is counted for scheduling
# capacity but is NEVER a scale-down candidate.
#
# ELASTIC-EXCLUSION INVARIANT (do not weaken without updating Task 16's CI
# drift guard too): Terraform in this directory NEVER enumerates Contabo's
# account-wide instance list — there is no `data "contabo_instance"` /
# for_each over "all instances" anywhere in terraform/contabo or
# modules/contabo-k3s-node. The only resource here is the explicitly named
# control-plane VPS. Because nothing here ever queries "give me every
# instance," `terraform apply` can never adopt, plan a diff against, or
# destroy an elastic (or consumer-dispatched) node — there is no mechanism by
# which it could. If a future change introduces any broad `data` source or
# for_each over Contabo's instance inventory, it MUST filter out instances
# tagged `fuzeinfra-elastic` before this module/state can safely touch them.
#
# So the "baseline" is IMPLICIT and DYNAMIC: it is "all non-elastic nodes,
# whatever/however many exist right now" (today: 1 control-plane + 2
# consumer-dispatched workers = 3, but that count is not fixed and this
# directory does not track it). The elastic pool is purely ADDITIVE on top:
# total nodes = current baseline + up to 2 elastic. There is no FuzeInfra-owned
# "baseline worker" resource here — provisioning consumer worker capacity is
# the consumer repo's job via dispatch, never this Terraform's.
# ---------------------------------------------------------------------------

terraform {
  required_version = ">= 1.6"

  required_providers {
    contabo = {
      source  = "contabo/contabo"
      version = "~> 0.1"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }

  # Uncomment to store state remotely (recommended for production):
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "fuzeinfra/contabo/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "contabo" {
  oauth2_client_id     = var.contabo_client_id
  oauth2_client_secret = var.contabo_client_secret
  oauth2_user          = var.contabo_api_user
  oauth2_pass          = var.contabo_api_password
}

# Cloudflare provider is active only when cloudflare_api_token is set.
# All cloudflare_* resources in cloudflare.tf are gated on local.cloudflare_enabled.
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

provider "null" {}
provider "local" {}
provider "random" {}

