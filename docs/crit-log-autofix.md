# CRIT Log → Claude Auto-Fix Pipeline

Grafana detects CRIT/FATAL/PANIC log lines in Loki and automatically dispatches a Claude Code agent to investigate and fix the root cause.

## Architecture

```
Loki logs
    ↓  (every 5 min)
Grafana unified alerting
    ↓  (severity=critical fired)
CF Worker crit-alert-bridge          ← bearer token auth
    ↓  (repository_dispatch)
GitHub Actions: grafana-crit-fix.yml
    ↓  kubectl port-forward → Loki HTTP API
    ↓  fetch last 30 min of CRIT log lines
    ↓  claude --print --max-turns 30
    ├── fix found → commit + PR + auto-merge
    └── no fix   → GitHub Issue + label "bug"
    ↓
Email → izzy.weinberg@gmail.com
```

## Why CF Worker as bridge (not direct Loki query)

The GHA workflow runner has existing `KUBE_CONFIG` access to the k3s cluster. Fetching logs via `kubectl port-forward → curl Loki HTTP API` reuses that access without exposing Loki publicly.

Alternative (querying Loki from the CF Worker directly) would require:
- Exposing a Loki HTTP endpoint through the CF tunnel with a separate Access bypass
- Managing Loki credentials in the Worker
- Loki queries that may time out in Worker's 30s CPU budget

The Worker stays thin: validate bearer token, check `status == "firing"`, forward alert metadata to GitHub. Log fetching and Claude analysis happen inside GHA where they belong.

## Components

### 1. Grafana Alert Rule (`helm/fuzeinfra/templates/configmaps-monitoring.yaml`)

- ConfigMap `fuzeinfra-grafana-alerting` with three provisioning files:
  - `contact-points.yaml` — webhook contact point `claude-autofix` → `https://crit-alert.prod.fuzefront.com/`
  - `policy.yaml` — routes `severity=critical` to `claude-autofix`, repeat every 4h
  - `rules.yaml` — LogQL: `sum(count_over_time({namespace=~".+"} |~ "(?i)(CRIT|CRITICAL|FATAL|PANIC)" [5m])) > 0`
- Alert evaluates every 5 minutes; fires immediately on first hit (`for: 0s`)
- `noDataState: OK` — quiet cluster never triggers

### 2. CF Worker Bridge (`terraform/contabo/crit-alert-bridge.js`)

- Cloudflare Worker at `crit-alert.prod.fuzefront.com`
- Validates `Authorization: Bearer $BRIDGE_TOKEN`
- Ignores resolved alerts (`status != "firing"`)
- Calls `POST /repos/izzywdev/FuzeInfra/dispatches` with event type `grafana-crit-alert`
- Worker secrets managed in Terraform state via `cloudflare_worker_script` bindings

### 3. GHA Workflow (`.github/workflows/grafana-crit-fix.yml`)

Triggered by `repository_dispatch` event type `grafana-crit-alert`.

Steps:
1. `kubectl` configured from `KUBE_CONFIG` secret (base64-decoded)
2. `kubectl port-forward svc/fuzeinfra-loki 3100:3100` → `curl` Loki query API for last 30 min
3. `claude --print --allowedTools "Bash,Read,Write,Edit,Glob,Grep" --max-turns 30` with structured prompt
4. Python extracts last `{"action":"..."}` JSON from Claude's stdout
5. `action=fix` → commit changes, push branch, `gh pr create`, `gh pr merge --auto --merge`
6. `action=issue` → `gh issue create --label bug`
7. Email via Gmail SMTP (`dawidd6/action-send-mail`)

### 4. Terraform Resources (`terraform/contabo/cloudflare.tf`)

- `cloudflare_worker_script.crit_alert_bridge` — deploys the Worker with secret bindings
- `cloudflare_worker_route.crit_alert_bridge` — routes `crit-alert.prod.fuzefront.com/*` to the Worker
- `cloudflare_zero_trust_access_application.crit_alert_bridge` — CF Access app for the endpoint
- `cloudflare_zero_trust_access_policy.crit_alert_bridge_bypass` — bypass policy so Grafana can POST without OTP
- `null_resource.crit_bridge_token_secret` — kubectl-patches `CRIT_BRIDGE_TOKEN` into `fuzeinfra-secrets`

All resources are conditional on `var.crit_bridge_token != ""`.

## Setup

### GitHub Secrets Required

| Secret | Value |
|--------|-------|
| `KUBE_CONFIG` | base64-encoded k3s kubeconfig (already set by terraform apply) |
| `ANTHROPIC_API_KEY` | Claude API key |
| `GMAIL_USERNAME` | `izzy.weinberg@gmail.com` |
| `GMAIL_APP_PASSWORD` | Gmail App Password (myaccount.google.com/apppasswords) |

### Terraform Variables Required (`terraform/contabo/terraform.tfvars`)

```hcl
# Generate with: openssl rand -hex 32
crit_bridge_token = "your-random-secret-here"
```

### Deploy

```bash
cd terraform/contabo

# Apply only the CRIT alert Worker resources (safe — no VPS or tunnel changes)
terraform apply \
  -target=cloudflare_worker_script.crit_alert_bridge \
  -target=cloudflare_worker_route.crit_alert_bridge \
  -target=cloudflare_zero_trust_access_application.crit_alert_bridge \
  -target=cloudflare_zero_trust_access_policy.crit_alert_bridge_bypass \
  -target=null_resource.crit_bridge_token_secret
```

Then merge the Helm changes via Git → ArgoCD auto-syncs Grafana with the alerting provisioning.

## Testing

Trigger a synthetic CRIT log to verify the pipeline end-to-end:

```bash
# Inject a CRIT log line into any pod
kubectl exec -n fuzeinfra deploy/fuzeinfra-grafana -c grafana -- \
  sh -c 'echo "CRITICAL: synthetic test alert $(date)" >&2'

# Wait up to 5 min for Grafana alert to fire
# Check GHA: Actions → Grafana CRIT Log Auto-Fix
```

Or POST directly to the Worker to test bypass + GHA dispatch:

```bash
curl -X POST https://crit-alert.prod.fuzefront.com/ \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"firing","alerts":[{"annotations":{"summary":"Test CRIT alert"},"startsAt":"2026-06-21T00:00:00Z","labels":{"severity":"critical"}}]}'
```
