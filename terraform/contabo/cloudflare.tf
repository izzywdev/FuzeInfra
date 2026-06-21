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
resource "cloudflare_zero_trust_tunnel_cloudflared" "fuzeinfra" {
  count      = local.cloudflare_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name
  secret     = random_bytes.tunnel_secret[0].base64
}

# Routing rules — managed on Cloudflare's side, fetched by cloudflared at startup.
# cloudflared runs as a pod inside k3s, so it can reach any k8s service by DNS.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "fuzeinfra" {
  count      = local.cloudflare_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id

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
  value   = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].cname
  type    = "CNAME"
  proxied = true
  ttl     = 1
}

# DNS: *.prod.fuzefront.com → same tunnel
resource "cloudflare_record" "prod_wildcard" {
  count   = local.cloudflare_enabled ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "*.${var.prod_subdomain}"
  value   = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].cname
  type    = "CNAME"
  proxied = true
  ttl     = 1
}

# Cloudflare Access: protect *.prod.fuzefront.com with email OTP.
# The apex prod.fuzefront.com is NOT matched by *.prod — it stays public.
resource "cloudflare_zero_trust_access_application" "admin_services" {
  count            = local.cloudflare_enabled ? 1 : 0
  account_id       = var.cloudflare_account_id
  name             = "FuzeInfra Admin Services"
  domain           = "*.${local.prod_domain}"
  type             = "self_hosted"
  session_duration = var.access_session_duration

  app_launcher_visible = false
}

resource "cloudflare_zero_trust_access_policy" "admin_email_otp" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.admin_services[0].id
  name           = "Admin email allowlist (OTP)"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.allowed_admin_emails
  }
}

# Cloudflare App Launcher — the portal itself at <team>.cloudflareaccess.com
# Needs its own access application + policy or CF shows "contact your admin".
resource "cloudflare_zero_trust_access_application" "app_launcher" {
  count            = local.cloudflare_enabled ? 1 : 0
  account_id       = var.cloudflare_account_id
  name             = "App Launcher"
  type             = "app_launcher"
  session_duration = var.access_session_duration
}

resource "cloudflare_zero_trust_access_policy" "app_launcher" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.app_launcher[0].id
  name           = "Admin email allowlist (OTP)"
  precedence     = 1
  decision       = "allow"

  include {
    email = var.allowed_admin_emails
  }
}

# Neo4j Browser static UI bypass
#
# Neo4j Browser's index.html loads JS modules with <script type="module" crossorigin>,
# which forces all dynamic imports to run with credentials:omit (no cookies).
# CF Access redirects cookie-less requests to cloudflareaccess.com, which causes
# a CORS failure in the browser — breaking the entire SPA before it mounts.
#
# Fix: bypass CF Access for neo4j.*/browser so the UI assets load freely.
# The database itself still requires Neo4j username/password over Bolt.
resource "cloudflare_zero_trust_access_application" "neo4j_browser_ui" {
  count            = local.cloudflare_enabled ? 1 : 0
  account_id       = var.cloudflare_account_id
  name             = "Neo4j Browser (public UI)"
  domain           = "neo4j.${local.prod_domain}/browser"
  type             = "self_hosted"
  session_duration = "0s"
  app_launcher_visible = false
}

resource "cloudflare_zero_trust_access_policy" "neo4j_browser_ui_bypass" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.neo4j_browser_ui[0].id
  name           = "Bypass — Neo4j Browser static UI"
  precedence     = 1
  decision       = "bypass"

  include {
    everyone = true
  }
}

# Cache Neo4j Browser static assets at the CF edge.
#
# Neo4j sets Cache-Control: no-store on ALL browser assets, which causes
# Cloudflare to forward every request to the origin (cf-cache: BYPASS).
# When the page loads, Vite's __vitePreload triggers a second wave of
# module-preload requests for the same files; Neo4j returns 503 under
# that concurrent burst, crashing the entire dynamic-import chain.
#
# Fix: override origin cache directives for /browser/assets/* and cache
# at the CF edge for 1 hour. These files are content-hashed (Vite build
# output), so they never change for a given filename — safe to cache.
resource "cloudflare_ruleset" "neo4j_browser_cache" {
  count   = local.cloudflare_enabled ? 1 : 0
  zone_id = var.cloudflare_zone_id
  name    = "Neo4j Browser Asset Cache"
  kind    = "zone"
  phase   = "http_request_cache_settings"

  rules {
    action = "set_cache_settings"
    action_parameters {
      cache = true
      edge_ttl {
        mode    = "override_origin"
        default = 3600
      }
      browser_ttl {
        mode    = "override_origin"
        default = 3600
      }
    }
    expression  = "(http.host eq \"neo4j.${local.prod_domain}\" and starts_with(http.request.uri.path, \"/browser/assets/\"))"
    description = "Cache Neo4j Browser static assets 1h — overrides origin no-store to prevent 503 on burst preload requests"
    enabled     = true
  }
}

# ---------------------------------------------------------------------------
# Cloudflare App Launcher bookmarks
#
# One tile per service. type = "bookmark" creates a clickable shortcut in the
# CF Access App Launcher — no extra access policy needed because the wildcard
# self_hosted app above already enforces OTP on every *.prod.fuzefront.com URL.
# ---------------------------------------------------------------------------
locals {
  launcher_services = {
    "argocd"        = { name = "ArgoCD",       logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/argo-cd.png",       path = "" }
    "grafana"       = { name = "Grafana",       logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/grafana.png",       path = "" }
    "prometheus"    = { name = "Prometheus",    logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/prometheus.png",    path = "" }
    "alertmanager"  = { name = "Alertmanager",  logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/alertmanager.png",  path = "" }
    "airflow"       = { name = "Airflow",       logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/apache-airflow.png",path = "" }
    "flower"        = { name = "Flower",        logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/celery.png",        path = "" }
    "kafka-ui"      = { name = "Kafka UI",      logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/kafka.png",         path = "" }
    "mongo-express" = { name = "Mongo Express", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/mongodb.png",       path = "" }
    "rabbitmq"      = { name = "RabbitMQ",      logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/rabbitmq.png",      path = "" }
    "neo4j"         = { name = "Neo4j",         logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/neo4j.png",         path = "" }
    "elasticsearch" = { name = "Elasticsearch", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/elasticsearch.png", path = "" }
    "chromadb"      = { name = "ChromaDB",      logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/chroma.png",        path = "/api/v2/heartbeat" }
  }
}

resource "cloudflare_zero_trust_access_application" "launcher_bookmark" {
  for_each         = nonsensitive(local.cloudflare_enabled) ? local.launcher_services : {}
  account_id       = var.cloudflare_account_id
  name             = each.value.name
  domain           = "https://${each.key}.${local.prod_domain}${each.value.path}"
  type             = "bookmark"
  app_launcher_visible = true
  logo_url         = each.value.logo
}

# Construct the cloudflared token from known fields.
# Format: base64(JSON{ a: account_id, t: tunnel_id, s: base64_secret })
locals {
  tunnel_token = local.cloudflare_enabled ? base64encode(jsonencode({
    a = var.cloudflare_account_id
    t = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id
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
    tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id
    token_hash = sha256(local.tunnel_token)
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
