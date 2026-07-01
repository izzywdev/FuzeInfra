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

  # Public vanity hosts served directly by the FuzeFront platform.
  # These live at the apex zone (e.g. app.fuzefront.com), OUTSIDE the
  # *.prod.fuzefront.com Access wildcard, so they are public by default —
  # Authentik handles platform auth, not Cloudflare Access. The FuzeFront
  # chart sets its Traefik Ingress host to match (className traefik, TLS off,
  # CF terminates edge TLS). Adding a future public host is a one-line edit.
  public_vanity_hosts = ["app", "auth"]
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
    # Generic catch-all: every other hostname → Traefik, which host-routes by
    # Ingress. This is domain-agnostic: any product on its own domain (its own
    # apex/subdomains) just CNAMEs that host to THIS tunnel and declares a Traefik
    # Ingress in its OWN repo — no per-product hostname is enumerated here.
    # Traefik returns 404 for unconfigured hosts, so this exposes nothing new.
    # CF Access still gates *.prod.fuzefront.com at the edge (see below); other
    # domains are public by default (Authentik / the app owns their auth).
    ingress_rule {
      service = "http://traefik.kube-system:80"
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

# DNS: public vanity hosts (app/auth.fuzefront.com) → same tunnel, proxied.
# Proxied so CF terminates TLS at the edge (Universal SSL covers the apex hosts)
# and the request reaches cloudflared → the matching ingress_rule above → Traefik.
# These hosts are NOT covered by the *.prod Access wildcard, so they are public.
resource "cloudflare_record" "vanity" {
  for_each = nonsensitive(local.cloudflare_enabled) ? toset(local.public_vanity_hosts) : toset([])
  zone_id  = var.cloudflare_zone_id
  name     = each.value
  value    = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].cname
  type     = "CNAME"
  proxied  = true
  ttl      = 1
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

  # NOTE: app_launcher_visible is INERT on a type=app_launcher resource —
  # Cloudflare's API always stores it false and ignores writes, so the provider's
  # computed default (true) vs the API (false) is a perpetual no-op diff. Ignore it
  # (the API owns this field). The portal's TILES are the `launcher_bookmark`
  # bookmark apps below — those carry app_launcher_visible=true and it sticks there.
  lifecycle {
    ignore_changes = [app_launcher_visible]
  }
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
  count                = local.cloudflare_enabled ? 1 : 0
  account_id           = var.cloudflare_account_id
  name                 = "Neo4j Browser (public UI)"
  domain               = "neo4j.${local.prod_domain}/browser"
  type                 = "self_hosted"
  session_duration     = "0s"
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

# Grafana build asset bypass — allows CF to cache /public/build/* at the edge.
#
# CF Access injects the CF_Authorization cookie on all authenticated requests.
# A request bearing any cookie gets CF-Cache-Status: BYPASS, meaning CF always
# forwards to the origin tunnel and never serves from cache. Grafana's content-
# hashed build files (/public/build/*.js, /public/build/*.css) are identical for
# every user; they need no authentication. Bypassing CF Access for this path lets
# CF cache them and serve subsequent requests from the edge, eliminating the burst
# of concurrent tunnel connections that causes 503 on the tablePanel CSS load.
resource "cloudflare_zero_trust_access_application" "grafana_build_assets" {
  count                = local.cloudflare_enabled ? 1 : 0
  account_id           = var.cloudflare_account_id
  name                 = "Grafana Build Assets (public)"
  domain               = "grafana.${local.prod_domain}/public/build"
  type                 = "self_hosted"
  session_duration     = "0s"
  app_launcher_visible = false
}

resource "cloudflare_zero_trust_access_policy" "grafana_build_assets_bypass" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.grafana_build_assets[0].id
  name           = "Bypass — Grafana static build assets"
  precedence     = 1
  decision       = "bypass"

  include {
    everyone = true
  }
}

# Sealed Secrets public cert bypass.
#
# sealed-secrets.prod.fuzefront.com/v1/cert.pem serves the Sealed Secrets
# controller's PUBLIC key. Consumers fetch it to seal secrets OFFLINE — they
# have no cluster access and no Cloudflare Access account, so this endpoint must
# be reachable by anyone (CI, scripts, developers). The cert is a public key:
# it can encrypt but never decrypt, so exposing it is safe by design.
#
# A more-specific hostname Access app takes precedence over the wildcard
# *.prod.fuzefront.com OTP app, so this bypass exempts ONLY the cert endpoint;
# the controller's private key and the rest of the cluster stay protected.
# See docs/SECRETS_MANAGEMENT.md.
resource "cloudflare_zero_trust_access_application" "sealed_secrets_cert" {
  count                = local.cloudflare_enabled ? 1 : 0
  account_id           = var.cloudflare_account_id
  name                 = "Sealed Secrets public cert (public)"
  domain               = "sealed-secrets.${local.prod_domain}"
  type                 = "self_hosted"
  session_duration     = "0s"
  app_launcher_visible = false
}

resource "cloudflare_zero_trust_access_policy" "sealed_secrets_cert_bypass" {
  count          = local.cloudflare_enabled ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.sealed_secrets_cert[0].id
  name           = "Bypass — Sealed Secrets public cert"
  precedence     = 1
  decision       = "bypass"

  include {
    everyone = true
  }
}

# Cache static build assets at the CF edge.
#
# One zone-level ruleset per phase is the CF limit, so Neo4j and Grafana rules
# live in the same ruleset. Both targets are content-hashed (filename = hash),
# so a 1-year TTL is safe — a file never changes under its hash-addressed name.
#
# Without caching: ~8–10 concurrent JS/CSS requests on every Grafana dashboard
# load all hit the CF tunnel simultaneously. CF coalesces duplicate-URL requests
# into one upstream fetch; if that fetch returns 503, all waiters see 503.
# The tablePanel CSS chunk is requested during the burst and reliably 503s,
# producing "Error loading: table" on every dashboard open.
#
# With caching: first request per file hits the tunnel (single fetch); all
# subsequent requests are served from the CF edge — no tunnel involved.
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

  rules {
    action = "set_cache_settings"
    action_parameters {
      cache = true
      edge_ttl {
        mode    = "override_origin"
        default = 31536000
      }
      browser_ttl {
        mode    = "override_origin"
        default = 31536000
      }
    }
    expression  = "(http.host eq \"grafana.${local.prod_domain}\" and starts_with(http.request.uri.path, \"/public/build/\"))"
    description = "Cache Grafana content-hashed build assets 1yr at CF edge — prevents 503 on concurrent tablePanel CSS load"
    enabled     = true
  }
}

# CF Worker: strip CF_Authorization cookie for Grafana /public/build/* static assets.
#
# Problem: the CF_Authorization cookie (set domain-wide by CF Access) is included in the
# cache key for every browser request. Even with a cache rule that forces caching, CF
# treats each unique cookie value as a separate cache entry — so every authenticated user's
# first page load hits the origin tunnel cold, which 503s under the ~8-request burst.
#
# Fix: Workers intercept requests BEFORE CF's cache. Stripping the auth cookie makes CF
# compute a cookie-free cache key → matches the shared HIT already warm for unauthenticated
# requests → served from edge without touching the tunnel.
#
# http_request_transform only allows URL rewrites (not header removal at pre-cache time).
# http_request_late_transform allows header removal but runs after cache — too late.
# Workers are the only free-plan mechanism that runs pre-cache with header mutation.
resource "cloudflare_worker_script" "grafana_asset_serve" {
  count      = local.cloudflare_enabled ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = "grafana-asset-serve"
  content    = file("${path.module}/grafana-asset-serve.js")
  module     = true
}

resource "cloudflare_worker_route" "grafana_build_assets" {
  count       = local.cloudflare_enabled ? 1 : 0
  zone_id     = var.cloudflare_zone_id
  pattern     = "grafana.${local.prod_domain}/public/build/*"
  script_name = cloudflare_worker_script.grafana_asset_serve[0].name
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
    "argocd"        = { name = "ArgoCD", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/argo-cd.png", path = "" }
    "grafana"       = { name = "Grafana", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/grafana.png", path = "" }
    "prometheus"    = { name = "Prometheus", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/prometheus.png", path = "" }
    "alertmanager"  = { name = "Alertmanager", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/alertmanager.png", path = "" }
    "airflow"       = { name = "Airflow", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/apache-airflow.png", path = "" }
    "flower"        = { name = "Flower", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/celery.png", path = "" }
    "kafka-ui"      = { name = "Kafka UI", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/kafka.png", path = "" }
    "mongo-express" = { name = "Mongo Express", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/mongodb.png", path = "" }
    "rabbitmq"      = { name = "RabbitMQ", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/rabbitmq.png", path = "" }
    "neo4j"         = { name = "Neo4j", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/neo4j.png", path = "" }
    "elasticsearch" = { name = "Elasticsearch", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/elasticsearch.png", path = "" }
    "chromadb"      = { name = "ChromaDB", logo = "https://cdn.jsdelivr.net/gh/walkxcode/dashboard-icons/png/chroma.png", path = "/api/v2/heartbeat" }
  }
}

resource "cloudflare_zero_trust_access_application" "launcher_bookmark" {
  for_each             = nonsensitive(local.cloudflare_enabled) ? local.launcher_services : {}
  account_id           = var.cloudflare_account_id
  name                 = each.value.name
  domain               = "https://${each.key}.${local.prod_domain}${each.value.path}"
  type                 = "bookmark"
  app_launcher_visible = true
  logo_url             = each.value.logo
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
# ---------------------------------------------------------------------------
# CRIT-Alert Bridge — Cloudflare Worker
#
# Grafana fires a webhook when severity=critical alert fires.
# This Worker validates the shared secret, drops "resolved" events, and
# calls GitHub repository_dispatch → triggers grafana-crit-fix.yml.
#
# Secret bindings keep credentials out of Worker env vars in cleartext.
# BRIDGE_TOKEN is also injected into fuzeinfra-secrets so Grafana can read it.
# ---------------------------------------------------------------------------
resource "cloudflare_worker_script" "crit_alert_bridge" {
  count      = local.cloudflare_enabled && var.crit_bridge_token != "" ? 1 : 0
  account_id = var.cloudflare_account_id
  name       = "crit-alert-bridge"
  content    = file("${path.module}/crit-alert-bridge.js")
  module     = true

  secret_text_binding {
    name = "GITHUB_TOKEN"
    text = var.github_token
  }

  plain_text_binding {
    name = "GITHUB_REPO"
    text = "${var.github_owner}/${var.github_repo}"
  }

  secret_text_binding {
    name = "BRIDGE_TOKEN"
    text = var.crit_bridge_token
  }
}

resource "cloudflare_worker_route" "crit_alert_bridge" {
  count       = local.cloudflare_enabled && var.crit_bridge_token != "" ? 1 : 0
  zone_id     = var.cloudflare_zone_id
  pattern     = "crit-alert.${local.prod_domain}/*"
  script_name = cloudflare_worker_script.crit_alert_bridge[0].name
}

# CF Access bypass — Grafana must POST without a browser OTP session.
# The wildcard *.prod.fuzefront.com Access app would block this endpoint.
# A more-specific hostname app takes precedence and lets the Worker handle auth
# itself (via BRIDGE_TOKEN).
resource "cloudflare_zero_trust_access_application" "crit_alert_bridge" {
  count                = local.cloudflare_enabled && var.crit_bridge_token != "" ? 1 : 0
  account_id           = var.cloudflare_account_id
  name                 = "CRIT Alert Bridge (public webhook)"
  domain               = "crit-alert.${local.prod_domain}"
  type                 = "self_hosted"
  session_duration     = "0s"
  app_launcher_visible = false
}

resource "cloudflare_zero_trust_access_policy" "crit_alert_bridge_bypass" {
  count          = local.cloudflare_enabled && var.crit_bridge_token != "" ? 1 : 0
  account_id     = var.cloudflare_account_id
  application_id = cloudflare_zero_trust_access_application.crit_alert_bridge[0].id
  name           = "Bypass — CRIT alert webhook (Worker handles auth)"
  precedence     = 1
  decision       = "bypass"

  include {
    everyone = true
  }
}

# ---------------------------------------------------------------------------
# fuzeinfra-tunnel-secrets — Terraform-owned Secret (ArgoCD never touches it)
#
# Separate from fuzeinfra-secrets (Helm/ArgoCD-owned) so that ArgoCD resyncs
# and full cluster wipes never wipe these keys. Any `terraform apply` after a
# fresh provision recreates this secret automatically via provision_id trigger.
#
# Keys:
#   CLOUDFLARE_TUNNEL_TOKEN       — bearer token for cloudflared token mode
#   CLOUDFLARE_TUNNEL_CREDENTIALS — JSON credentials for local-config mode
#   CRIT_BRIDGE_TOKEN             — shared secret for the CF Worker webhook bridge
# ---------------------------------------------------------------------------
locals {
  tunnel_credentials_json = local.cloudflare_enabled ? jsonencode({
    AccountTag   = var.cloudflare_account_id
    TunnelID     = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id
    TunnelSecret = random_bytes.tunnel_secret[0].base64
  }) : ""
}

resource "null_resource" "tunnel_secrets" {
  count      = local.cloudflare_enabled ? 1 : 0
  depends_on = [null_resource.extract_kubeconfig]

  triggers = {
    # Re-run on: new provision, tunnel rotation, or crit-bridge token change.
    provision_id = null_resource.provision.id
    tunnel_id    = cloudflare_zero_trust_tunnel_cloudflared.fuzeinfra[0].id
    token_hash   = sha256(local.tunnel_token)
    crit_hash    = sha256(var.crit_bridge_token)
  }

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = <<-EOT
      export KUBECONFIG="${path.root}/k3s-kubeconfig.yaml"

      # Wait for the namespace to exist (ArgoCD PreSync must have run).
      for i in $(seq 1 30); do
        kubectl get namespace fuzeinfra &>/dev/null && break
        echo "  Waiting for fuzeinfra namespace ($i/30)..."
        sleep 10
      done

      TOKEN_B64=$(printf '%s' '${local.tunnel_token}' | base64 -w0 2>/dev/null || printf '%s' '${local.tunnel_token}' | base64)
      CREDS_B64=$(printf '%s' '${local.tunnel_credentials_json}' | base64 -w0 2>/dev/null || printf '%s' '${local.tunnel_credentials_json}' | base64)
      CRIT_B64=$(printf '%s' '${var.crit_bridge_token}' | base64 -w0 2>/dev/null || printf '%s' '${var.crit_bridge_token}' | base64)

      kubectl create secret generic fuzeinfra-tunnel-secrets \
        -n fuzeinfra \
        --from-literal=CLOUDFLARE_TUNNEL_TOKEN=placeholder \
        --from-literal=CLOUDFLARE_TUNNEL_CREDENTIALS=placeholder \
        --from-literal=CRIT_BRIDGE_TOKEN=placeholder \
        --dry-run=client -o yaml \
      | kubectl apply -f -

      kubectl patch secret fuzeinfra-tunnel-secrets -n fuzeinfra \
        --type=merge \
        -p "{\"data\":{\"CLOUDFLARE_TUNNEL_TOKEN\":\"$${TOKEN_B64}\",\"CLOUDFLARE_TUNNEL_CREDENTIALS\":\"$${CREDS_B64}\",\"CRIT_BRIDGE_TOKEN\":\"$${CRIT_B64}\"}}"

      echo "fuzeinfra-tunnel-secrets created/updated."
    EOT
  }
}
