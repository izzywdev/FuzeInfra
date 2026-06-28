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

## Per-repo routing (which repo gets the issue?)

The shared cluster hosts many consumers, each in its own namespace. A CRIT log
should land in the issue tracker of whoever **owns** the emitting service — not
always FuzeInfra.

The owning repo is declared by an annotation on the namespace:

```
fuzeinfra.io/owner-repo: <owner>/<repo>
```

`grafana-crit-fix.yml` resolves it (it already has cluster access via
`KUBE_CONFIG`) — the CF Worker stays thin and never queries the cluster:

1. Parse `namespace` from the alert labels (`client_payload.labels`, treated as data).
2. `kubectl get ns "$ns" -o jsonpath='{.metadata.annotations.fuzeinfra\.io/owner-repo}'`.
3. Validate it matches `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`.
4. File the issue there: `gh issue create --repo "$owner_repo" …` (with cross-repo dedup).

**Fallback** to `izzywdev/FuzeInfra` when the namespace is missing/unknown, not
found, unannotated, or the value is invalid.

**`fix` vs `issue` vs handoff, by owner:**
- **FuzeInfra-owned** crit (`is_self`): unchanged — `fix` opens a PR here, `issue`
  files here, `ignore` requests suppression here.
- **Consumer-owned** crit (a code-fix PR can't target another repo's checkout):
  the handler performs a **cross-repo handoff** — it (1) records a FuzeInfra
  **tracking** issue, (2) opens an issue in the owner repo that **`@claude`-mentions**
  so the owner's own handler fixes the root cause and deploys, (3) cross-links the
  two, and (4) **closes the FuzeInfra tracking issue** as handed-off. `action=fix`
  on a consumer namespace is first downgraded into this handoff path. Dedup is on
  the owner repo (skip if it already has an open `crit-autofix` issue).
- `action=ignore` always stays in FuzeInfra — the suppression list is FuzeInfra's.

### Onboarding a consumer namespace

Annotate the namespace once so its crit logs route to its own repo:

```bash
kubectl annotate namespace <namespace> \
  fuzeinfra.io/owner-repo=<owner>/<repo> --overwrite
# e.g.
kubectl annotate namespace fuzefront \
  fuzeinfra.io/owner-repo=izzywdev/FuzeFront --overwrite
```

FuzeInfra's own `fuzeinfra` namespace is annotated automatically by Argo CD
(`managedNamespaceMetadata` in `argocd/applications/fuzeinfra-prod.yaml`).

> **Token scope:** filing into a consumer repo needs the workflow's `GH_TOKEN`
> (a PAT) to have `issues:write` there — the default `GITHUB_TOKEN` is scoped to
> FuzeInfra only. The workflow uses `secrets.GH_TOKEN || secrets.GITHUB_TOKEN`;
> set `GH_TOKEN` to a PAT spanning the consumer repos (the existing automation
> PAT covers `izzywdev/*`).

### Traceability

Every auto-opened issue/PR links the **specific run** (not just the workflow) via
a `**Handling run:**` line + footer, so you can trace which run produced it.

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
5. `action=fix` → (FuzeInfra-owned namespaces only) commit changes, push branch, `gh pr create`, `gh pr merge --auto --merge`
6. `action=issue` → `gh issue create --repo <owner-repo> --label bug` (routed to the namespace owner — see [Per-repo routing](#per-repo-routing-which-repo-gets-the-issue))
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
