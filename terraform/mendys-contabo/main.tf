# ---------------------------------------------------------------------------
# MendysRobotics — Contabo VPS / k3s provisioning root
#
# FuzeInfra owns all infrastructure EXECUTION for the Fuze family. This module
# provisions the MendysRobotics production cluster: a DEDICATED Contabo VPS,
# SEPARATE from FuzeInfra's own prod VPS (different server, different state key).
#
# Stack provisioned here:
#   - Contabo VPS (Ubuntu)
#   - k3s with Traefik DISABLED (ingress-nginx replaces it)
#   - ingress-nginx + cert-manager + Let's Encrypt HTTP-01 ClusterIssuer
#   - ArgoCD, then the MendysRobotics app-of-apps (pulled from izzywdev/MendysRobotics)
#   - Cloudflare DNS A-records (proxied = false so HTTP-01 + direct ingress work)
#
# Unlike FuzeInfra's own cluster, Mendys uses DIRECT ingress (ports 80/443 open)
# + Let's Encrypt — NOT the Cloudflare Tunnel. Cloudflare here is DNS-only.
#
# The Helm/Kustomize/ArgoCD DECLARATIONS live in izzywdev/MendysRobotics under
# deploy/. This module only executes them; it does not duplicate them.
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
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }
}

provider "contabo" {
  oauth2_client_id     = var.contabo_client_id
  oauth2_client_secret = var.contabo_client_secret
  oauth2_user          = var.contabo_api_user
  oauth2_pass          = var.contabo_api_password
}

# DNS-only Cloudflare usage (no Tunnel, no Access). All records are proxied=false
# so Let's Encrypt HTTP-01 challenges reach ingress-nginx directly.
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

provider "null" {}
provider "local" {}
