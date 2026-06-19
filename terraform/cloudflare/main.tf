# ---------------------------------------------------------------------------
# Standalone Cloudflare module
#
# For CONTABO DEPLOYMENTS: use terraform/contabo/ instead.
# That module bundles these Cloudflare resources so a single `terraform apply`
# provisions the VPS, k3s, ArgoCD, tunnel, DNS, Access, and token injection.
#
# Use this standalone module only when managing Cloudflare resources
# independently of a Contabo server (e.g. for a different cluster).
# ---------------------------------------------------------------------------
terraform {
  required_version = ">= 1.6"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Store state alongside the contabo module (or use a shared remote backend).
  # Uncomment to store state remotely:
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "fuzeinfra/cloudflare/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# ---------------------------------------------------------------------------
# Named Tunnel
# ---------------------------------------------------------------------------
# A 32-byte cryptographically random secret that authenticates cloudflared.
resource "random_bytes" "tunnel_secret" {
  length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "fuzeinfra" {
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name

  # The secret must be a base64-encoded 32-byte value.
  secret = random_bytes.tunnel_secret.base64
}

# ---------------------------------------------------------------------------
# Tunnel routing (ingress rules — managed on Cloudflare, fetched by cloudflared)
# ---------------------------------------------------------------------------
# cloudflared runs inside the k3s cluster and can reach any in-cluster service
# by Kubernetes DNS (service.namespace.svc.cluster.local).
# The rules are evaluated top-to-bottom; the first match wins.

locals {
  prod_domain = "${var.prod_subdomain}.${var.zone_name}"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "fuzeinfra" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra.id

  config {
    # ArgoCD lives in the 'argocd' namespace — route directly to skip Traefik.
    ingress_rule {
      hostname = "argocd.${local.prod_domain}"
      service  = "http://argocd-server.argocd:80"
    }

    # All *.prod.fuzefront.com → Traefik ingress controller.
    # Traefik uses the Helm-generated Ingress rules (based on global.domain).
    ingress_rule {
      hostname = "*.${local.prod_domain}"
      service  = "http://traefik.kube-system:80"
    }

    # The apex prod.fuzefront.com routes to Traefik too (FuzeFront app, public).
    ingress_rule {
      hostname = local.prod_domain
      service  = "http://traefik.kube-system:80"
    }

    # Mandatory catch-all — cloudflared requires this.
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# ---------------------------------------------------------------------------
# DNS — point prod.fuzefront.com + wildcard at the tunnel CNAME
# ---------------------------------------------------------------------------
# prod.fuzefront.com  →  <tunnel-id>.cfargotunnel.com  (proxied through CF)
resource "cloudflare_record" "prod_apex" {
  zone_id = var.cloudflare_zone_id
  name    = var.prod_subdomain
  value   = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra.cname
  type    = "CNAME"
  proxied = true
  ttl     = 1
}

# *.prod.fuzefront.com  →  same tunnel
resource "cloudflare_record" "prod_wildcard" {
  zone_id = var.cloudflare_zone_id
  name    = "*.${var.prod_subdomain}"
  value   = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra.cname
  type    = "CNAME"
  proxied = true
  ttl     = 1
}

# ---------------------------------------------------------------------------
# Cloudflare Access — protect admin UIs behind email One-Time PIN
# ---------------------------------------------------------------------------
# One Access Application covers all subdomains (not the apex).
# *.prod.fuzefront.com matches argocd., grafana., airflow., etc.
# The apex prod.fuzefront.com is NOT covered → public (FuzeFront app).
resource "cloudflare_zero_trust_access_application" "admin_services" {
  account_id       = var.cloudflare_account_id
  name             = "FuzeInfra Admin Services"
  domain           = "*.${local.prod_domain}"
  type             = "self_hosted"
  session_duration = var.access_session_duration

  # Disable the default Cloudflare-hosted login page branding.
  app_launcher_visible = false
}

# Allow specific admin email addresses via One-Time PIN.
resource "cloudflare_zero_trust_access_policy" "admin_email_otp" {
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.admin_services.id
  name           = "Admin email allowlist (OTP)"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.allowed_admin_emails
  }
}

# ---------------------------------------------------------------------------
# Token construction
# cloudflared authenticates with a token that encodes: account, tunnel ID,
# and the tunnel secret. Terraform constructs it so it can be stored in the
# cluster without requiring the user to copy it from the dashboard.
# ---------------------------------------------------------------------------
locals {
  # cloudflared token format: base64(JSON{a: account, t: tunnel_id, s: b64_secret})
  tunnel_token = base64encode(jsonencode({
    a = var.cloudflare_account_id
    t = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra.id
    s = random_bytes.tunnel_secret.base64
  }))
}
