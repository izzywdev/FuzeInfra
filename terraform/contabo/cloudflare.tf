# ---------------------------------------------------------------------------
# Cloudflare Named Tunnel + Zero Trust Access
#
# All resources are conditional on cloudflare_api_token being set.
# When the token is provided, a single `terraform apply` in this directory:
#   1. Creates the Named Tunnel in Cloudflare Zero Trust
#   2. Configures routing rules (hostname → in-cluster service)
#   3. Creates DNS records: prod.fuzefront.com + *.prod.fuzefront.com
#   4. Creates a Cloudflare Access app protecting *.prod.fuzefront.com
#   5. Patches the cluster secret so cloudflared can connect immediately
#
# Set these in terraform.tfvars (never commit them):
#   cloudflare_api_token    = "..."
#   cloudflare_account_id   = "..."
#   cloudflare_zone_id      = "..."
# ---------------------------------------------------------------------------

locals {
  cloudflare_enabled = var.cloudflare_api_token != ""
  prod_domain        = "${var.prod_subdomain}.${var.zone_name}"
}

# 32-byte cryptographically random tunnel secret
resource "random_bytes" "tunnel_secret" {
  count  = local.cloudflare_enabled ? 1 : 0
  length = 32
}

# Named Tunnel (shows in Zero Trust dashboard as var.tunnel_name)
resource "cloudflare_tunnel" "fuzeinfra" {
  count      = local.cloudflare_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name
  secret     = random_bytes.tunnel_secret[0].base64
}

# Routing rules — managed on Cloudflare's side, fetched by cloudflared at startup.
# cloudflared runs as a pod inside k3s, so it can reach any k8s service by DNS.
resource "cloudflare_tunnel_config" "fuzeinfra" {
  count      = local.cloudflare_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_tunnel.fuzeinfra[0].id

  config {
    # ArgoCD is in the 'argocd' namespace — route directly, bypassing Traefik.
    ingress_rule {
      hostname = "argocd.${local.prod_domain}"
      service  = "http://argocd-server.argocd:80"
    }
    # All other *.prod.fuzefront.com → Traefik, which uses the Helm Ingress rules.
    ingress_rule {
      hostname = "*.${local.prod_domain}"
      service  = "http://traefik.kube-system:80"
    }
    # Apex prod.fuzefront.com → Traefik (FuzeFront app, public).
    ingress_rule {
      hostname = local.prod_domain
      service  = "http://traefik.kube-system:80"
    }
    # Mandatory catch-all.
    ingress_rule {
      service = "http_status:404"
    }
  }
}

# DNS: prod.fuzefront.com → tunnel CNAME (proxied through Cloudflare)
resource "cloudflare_record" "prod_apex" {
  count   = local.cloudflare_enabled ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = var.prod_subdomain
  value   = cloudflare_tunnel.fuzeinfra[0].cname
  type    = "CNAME"
  proxied = true
}

# DNS: *.prod.fuzefront.com → same tunnel
resource "cloudflare_record" "prod_wildcard" {
  count   = local.cloudflare_enabled ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "*.${var.prod_subdomain}"
  value   = cloudflare_tunnel.fuzeinfra[0].cname
  type    = "CNAME"
  proxied = true
}

# Cloudflare Access: protect *.prod.fuzefront.com with email OTP.
# The apex prod.fuzefront.com is NOT matched by *.prod — it stays public.
resource "cloudflare_access_application" "admin_services" {
  count            = local.cloudflare_enabled ? 1 : 0
  account_id       = var.cloudflare_account_id
  name             = "FuzeInfra Admin Services"
  domain           = "*.${local.prod_domain}"
  type             = "self_hosted"
  session_duration = var.access_session_duration

  app_launcher_visible = false
}

resource "cloudflare_access_policy" "admin_email_otp" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_access_application.admin_services[0].id
  name           = "Admin email allowlist (OTP)"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.allowed_admin_emails
  }
}

# Construct the cloudflared token from known fields.
# Format: base64(JSON{ a: account_id, t: tunnel_id, s: base64_secret })
locals {
  tunnel_token = local.cloudflare_enabled ? base64encode(jsonencode({
    a = var.cloudflare_account_id
    t = cloudflare_tunnel.fuzeinfra[0].id
    s = random_bytes.tunnel_secret[0].base64
  })) : ""
}

# ---------------------------------------------------------------------------
# Push token into the cluster secret so cloudflared connects on first sync.
# Runs after kubeconfig is available locally (extract_kubeconfig).
# ---------------------------------------------------------------------------
resource "null_resource" "cloudflare_tunnel_token" {
  count      = local.cloudflare_enabled ? 1 : 0
  depends_on = [null_resource.extract_kubeconfig]

  triggers = {
    # Re-run whenever tunnel ID or secret changes (e.g. after taint + rotate).
    tunnel_id   = cloudflare_tunnel.fuzeinfra[0].id
    token_hash  = sha256(local.tunnel_token)
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      export KUBECONFIG="${path.root}/k3s-kubeconfig.yaml"
      # Wait for the secret to exist (ArgoCD must have synced the Helm release).
      for i in $(seq 1 30); do
        kubectl get secret fuzeinfra-secrets -n fuzeinfra &>/dev/null && break
        echo "  Waiting for fuzeinfra-secrets ($i/30)..."
        sleep 10
      done
      TOKEN_B64=$(printf '%s' '${local.tunnel_token}' | base64 -w0 2>/dev/null || printf '%s' '${local.tunnel_token}' | base64)
      kubectl patch secret fuzeinfra-secrets -n fuzeinfra \
        --type=merge \
        -p "{\"data\":{\"CLOUDFLARE_TUNNEL_TOKEN\":\"$${TOKEN_B64}\"}}"
      echo "Cloudflare tunnel token stored in fuzeinfra-secrets."
    EOT
  }
}
