# Cloudflare Zero Trust — FuzeInfra Production Setup

All external access to the Contabo k3s cluster flows through Cloudflare:

```
Browser
  → Cloudflare WAF (DDoS, bot protection)
  → Cloudflare Access (email OTP — admin UIs only)
  → Cloudflare Named Tunnel
  → cloudflared pod (inside k3s, outbound-only connection)
  → Traefik ingress controller (in-cluster)
  → FuzeInfra services
```

No inbound ports 80/443 are open on the VPS. Traffic enters exclusively
through the Named Tunnel, which cloudflared establishes as outbound-only
connections to Cloudflare's edge.

---

## URL map

| Hostname | Route | Access required |
|---|---|---|
| `prod.fuzefront.com` | Traefik → (FuzeFront app) | No — public |
| `argocd.prod.fuzefront.com` | argocd-server.argocd:80 (direct) | Yes |
| `grafana.prod.fuzefront.com` | Traefik → fuzeinfra-grafana:3000 | Yes |
| `airflow.prod.fuzefront.com` | Traefik → fuzeinfra-airflow-webserver:8080 | Yes |
| `flower.prod.fuzefront.com` | Traefik → fuzeinfra-airflow-flower:5555 | Yes |
| `prometheus.prod.fuzefront.com` | Traefik → fuzeinfra-prometheus:9090 | Yes |
| `alertmanager.prod.fuzefront.com` | Traefik → fuzeinfra-alertmanager:9093 | Yes |
| `kafka-ui.prod.fuzefront.com` | Traefik → fuzeinfra-kafka-ui:8080 | Yes |
| `mongo-express.prod.fuzefront.com` | Traefik → fuzeinfra-mongo-express:8081 | Yes |
| `rabbitmq.prod.fuzefront.com` | Traefik → fuzeinfra-rabbitmq:15672 | Yes |

"Access required" means Cloudflare Access challenges the user for an
email One-Time PIN before forwarding the request to the tunnel.

---

## Prerequisites

1. A Cloudflare account with `fuzefront.com` in the account's zones
2. A Cloudflare API token with the following permissions:
   - **Zone > DNS > Edit** — creates CNAME records
   - **Account > Cloudflare Tunnel > Edit** — creates Named Tunnels
   - **Account > Access: Apps and Policies > Edit** — creates Access apps
   
   Create at: [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens)

3. Terraform >= 1.6 and the Cloudflare provider (downloaded on `init`)

---

## First-time setup (new deployment)

Cloudflare resources are bundled into the Contabo Terraform module.
**One `terraform apply` provisions everything** — VPS, k3s, ArgoCD,
Cloudflare tunnel, DNS, Access, and token injection:

```bash
cd terraform/contabo
cp terraform.tfvars.example terraform.tfvars

# Fill in Contabo credentials + SSH key + GitHub token, then add:
#   cloudflare_api_token  = "<your CF API token>"
#   cloudflare_account_id = "<your CF account ID>"
#   cloudflare_zone_id    = "<fuzefront.com zone ID>"
vim terraform.tfvars

terraform init
terraform apply
```

Terraform will (in dependency order):
1. Create the Contabo VPS
2. Install k3s + ArgoCD via SSH
3. Create the Cloudflare Named Tunnel, DNS records, and Access policy
4. Extract the kubeconfig locally
5. Wait for `fuzeinfra-secrets` to appear (ArgoCD syncs it)
6. Patch the tunnel token into the cluster secret
7. cloudflared restarts and connects — the tunnel is live

```
terraform output argocd_url_public
# → https://argocd.prod.fuzefront.com
```

---

## Existing deployment (apply Cloudflare to a running cluster)

The contabo Terraform state already exists and the VPS is up.
Add the Cloudflare variables to your `terraform.tfvars` and re-apply:

```bash
cd terraform/contabo
# Add to terraform.tfvars:
#   cloudflare_api_token  = "..."
#   cloudflare_account_id = "..."
#   cloudflare_zone_id    = "..."

terraform apply
```

Terraform will create only the new Cloudflare resources (the VPS and
k3s steps are idempotent — they no-op because triggers haven't changed).
The token is automatically injected into the running cluster.

ArgoCD auto-syncs and starts the cloudflared Deployment.

---

## Standalone Cloudflare module

`terraform/cloudflare/` is a standalone Terraform module for creating
Cloudflare resources without the full Contabo provisioning. Use it if
you need to point an existing cluster (non-Contabo) at a new tunnel, or
to manage Cloudflare resources independently.

```bash
cd terraform/cloudflare
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
# then inject the token manually:
TOKEN=$(terraform output -raw tunnel_token)
KUBECONFIG=../contabo/k3s-kubeconfig.yaml \
  scripts/setup-cloudflare-tunnel.sh "$TOKEN"
```

For Contabo deployments, prefer the bundled approach above.

---

## How the components fit together

### cloudflared Deployment (Helm → dns-tunnel.yaml)

`helm/fuzeinfra/values-contabo.yaml` sets:
- `cloudflareTunnel.enabled: true`
- `global.domain: prod.fuzefront.com`

The `TUNNEL_TOKEN` env var is sourced from `fuzeinfra-secrets` (key:
`CLOUDFLARE_TUNNEL_TOKEN`). This secret key is NOT rendered by Helm when
`credentials.cloudflare.tunnelToken` is empty (which it always is in git —
the token is injected by Terraform/scripts).

### Tunnel routing (cloudflare_tunnel_config in Terraform)

Rules are stored on Cloudflare and fetched by cloudflared at startup:

1. `argocd.prod.fuzefront.com` → `http://argocd-server.argocd:80` (direct, bypasses Traefik)
2. `*.prod.fuzefront.com` → `http://traefik.kube-system:80` (Traefik routes via Ingress rules)
3. `prod.fuzefront.com` → `http://traefik.kube-system:80` (FuzeFront app, public)
4. catch-all → `http_status:404`

### Ingress (helm/fuzeinfra/templates/ingress.yaml)

With `global.domain: prod.fuzefront.com`, the Helm Ingress creates rules for
`grafana.prod.fuzefront.com`, `airflow.prod.fuzefront.com`, etc. Traefik
matches these by hostname and proxies to the corresponding service.

### Cloudflare Access

One Access application covers `*.prod.fuzefront.com` (all subdomains).
When a browser hits `grafana.prod.fuzefront.com`, Cloudflare's edge:
1. Checks whether the user has a valid Access session cookie
2. If not, challenges via email One-Time PIN
3. Allowed emails are managed in `terraform/cloudflare/terraform.tfvars`

`prod.fuzefront.com` (the apex) is NOT covered by `*.prod.fuzefront.com`
— it remains public for end-users.

### Firewall

UFW on the VPS is configured by `terraform/contabo/provisioning.tf`:
- Port 22 open (SSH for admin)
- Port 6443 open (k3s API for kubectl/ArgoCD)
- Ports 80/443 **closed** from the public internet
- Outbound: all allowed (cloudflared needs outbound to Cloudflare's edge)

Even if someone knows the VPS IP, they cannot reach services on 80/443
directly — all requests must come through the Cloudflare tunnel.

---

## Updating the Access policy

To add or remove admin email addresses:

```bash
cd terraform/cloudflare
# Edit terraform.tfvars: add/remove emails from allowed_admin_emails
terraform apply
```

---

## Tunnel health monitoring

```bash
export KUBECONFIG=terraform/contabo/k3s-kubeconfig.yaml
# Live logs
kubectl logs -n fuzeinfra -l app.kubernetes.io/name=cloudflare-tunnel -f
# Pod status
kubectl get pods -n fuzeinfra | grep cloudflare
# Prometheus metrics (via tunnel itself)
kubectl port-forward -n fuzeinfra svc/fuzeinfra-cloudflare-tunnel 2000:2000
curl http://localhost:2000/metrics
```

---

## Rotating the tunnel token

```bash
cd terraform/cloudflare
# taint the random_bytes resource to force a new secret
terraform taint random_bytes.tunnel_secret
terraform apply   # creates new tunnel + token

cd ../..
TOKEN=$(cd terraform/cloudflare && terraform output -raw tunnel_token)
KUBECONFIG=terraform/contabo/k3s-kubeconfig.yaml \
  scripts/setup-cloudflare-tunnel.sh "$TOKEN"
```

Note: rotating the tunnel secret creates a NEW tunnel. Cloudflare DNS records
are automatically updated by Terraform to point to the new tunnel.

---

## Relationship to fuzefront.com (AWS CloudFront)

`fuzefront.com` and `www.fuzefront.com` continue to point to the AWS
CloudFront distribution serving the static marketing/landing page.

`prod.fuzefront.com` is a separate subdomain managed by this Cloudflare
tunnel config. The two are fully independent — both can exist in the same
Cloudflare zone without conflict.
