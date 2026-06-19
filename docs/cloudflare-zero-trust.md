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

### Step 1 — Create Cloudflare resources

```bash
cd terraform/cloudflare
cp terraform.tfvars.example terraform.tfvars
# Fill in: cloudflare_api_token, cloudflare_account_id, cloudflare_zone_id
vim terraform.tfvars

terraform init
terraform apply
```

This creates:
- Named Tunnel `fuzeinfra-prod`
- DNS CNAME records: `prod.fuzefront.com` and `*.prod.fuzefront.com`
- Cloudflare Access app protecting `*.prod.fuzefront.com` (email OTP)
- Outputs the `tunnel_token` needed by cloudflared

### Step 2 — Provision the cluster with the tunnel token

```bash
cd ../contabo
# Add to terraform.tfvars:
#   cloudflare_tunnel_token = "<paste tunnel_token from step 1>"
#
# Get it with:
#   terraform -chdir=../cloudflare output -raw tunnel_token
echo "cloudflare_tunnel_token = \"$(terraform -chdir=../cloudflare output -raw tunnel_token)\"" \
  >> terraform.tfvars

terraform apply
```

Provisioning will:
- Install k3s, ArgoCD
- Deploy the Helm chart (with `cloudflareTunnel.enabled: true`)
- Store the tunnel token in `fuzeinfra-secrets`
- Apply the ArgoCD Ingress for `argocd.prod.fuzefront.com`

---

## Existing deployment (token injection only)

If k3s is already running (as it is today):

```bash
# 1. Apply Cloudflare resources
cd terraform/cloudflare
terraform init && terraform apply

# 2. Inject token into the running cluster
cd ../../
KUBECONFIG=terraform/contabo/k3s-kubeconfig.yaml \
  TOKEN=$(cd terraform/cloudflare && terraform output -raw tunnel_token) \
  scripts/setup-cloudflare-tunnel.sh "$TOKEN"

# 3. Apply ArgoCD ingress for the new hostname
export KUBECONFIG=terraform/contabo/k3s-kubeconfig.yaml
kubectl apply -f argocd/argocd-ingress-prod.yaml

# 4. Update ArgoCD external URL
kubectl -n argocd patch configmap argocd-cm --type merge \
  -p '{"data":{"url":"https://argocd.prod.fuzefront.com"}}'
kubectl -n argocd rollout restart deployment/argocd-server
```

ArgoCD auto-syncs and starts the cloudflared Deployment (it was previously
disabled with `cloudflareTunnel.enabled: false` in values-contabo.yaml).

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
