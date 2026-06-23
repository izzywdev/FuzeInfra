# Infra-Request Dispatch (consumer → FuzeInfra)

Consumer repos (FuzeFront and future ones) **declare** their infrastructure
needs in git and **dispatch** to FuzeInfra to reconcile them. **FuzeInfra is the
sole credential holder** — consumers never see Contabo creds, the k3s node-token,
or the Terraform state backend.

```
┌────────────────────┐   repository_dispatch        ┌──────────────────────────┐
│ Consumer repo      │   (event_type:               │ FuzeInfra                │
│ (e.g. FuzeFront)   │    infra-request)            │                          │
│                    │ ───────────────────────────► │ infra-request-handler.yml│
│ deploy/terraform/  │   client_payload =           │  • validate vs whitelist │
│   node-request.tf  │   { repo, ref, requests[] }  │  • whitelisted → apply   │
│ deploy/argocd/     │                              │  • else → gated PR (plan)│
│ .github/workflows/ │                              │                          │
│   infra-dispatch.yml                              │ creds: FuzeInfra CI only │
└────────────────────┘                              └──────────────────────────┘
```

## Components in this repo

| Path | Role |
|------|------|
| [`modules/contabo-k3s-node`](../modules/contabo-k3s-node/) | TF module: VPS + cloud-init k3s agent join + labels (consumers reference this) |
| `.github/workflows/infra-request-handler.yml` | Handler (see activation note below) |
| [`config/infra-request-whitelist.json`](../config/infra-request-whitelist.json) | Auto-apply rules |
| [`scripts-tools/validate_infra_request.py`](../scripts-tools/validate_infra_request.py) | Whitelist validator used by the handler |

> **Activation note:** the handler workflow ships at
> [`docs/workflows/infra-request-handler.yml`](workflows/infra-request-handler.yml)
> because the bot that authored it cannot write under `.github/workflows/`. A
> maintainer must move it into place:
> ```bash
> git mv docs/workflows/infra-request-handler.yml .github/workflows/
> git commit -m "ci: activate infra-request-handler" && git push
> ```

## The auto-apply whitelist

A dispatched request is **auto-applied** only when it passes **every** rule in
[`config/infra-request-whitelist.json`](../config/infra-request-whitelist.json):

- `repo` ∈ `allowed_repos`
- each node's `product_id` ∈ `allowed_product_ids`
- each node's `region` ∈ `allowed_regions` (EU only by default)
- each node's `role` ∈ `allowed_roles` (`workload` only)
- node count ≤ `max_nodes_per_request`

Any violation → the handler opens a **gated PR carrying `terraform plan`**.
Merging the PR is the manual approval; nothing applies automatically.

Edit the JSON to widen/narrow what auto-applies — no workflow change needed.

## Scoped dispatch token — `FUZEINFRA_DISPATCH_TOKEN`

The consumer needs a credential that can do **exactly one thing**: trigger
`repository_dispatch` on FuzeInfra. It must **not** be able to read code, push,
or touch secrets. Two options:

### Option A — Fine-grained PAT (simplest)

1. GitHub → **Settings → Developer settings → Fine-grained personal access tokens → Generate new token**.
2. **Resource owner:** `izzywdev` (owner of FuzeInfra).
3. **Repository access:** *Only select repositories* → **FuzeInfra**.
4. **Permissions:** Repository permissions → **Contents: Read and write**
   *(the minimum that allows `POST /repos/{owner}/{repo}/dispatches`; no other
   scope is needed — leave everything else "No access")*.
5. Set a short expiry and a calendar reminder to rotate.
6. Copy the token.

### Option B — GitHub App (better for orgs / rotation)

1. Create a GitHub App owned by `izzywdev` with **Repository permissions →
   Contents: Read & write** only.
2. Install it on **FuzeInfra only**.
3. In the consumer workflow, mint a short-lived installation token (e.g.
   `actions/create-github-app-token`) and use it as the dispatch token.

### Store it in the consumer repo

Add the token to the **consumer** repo (e.g. FuzeFront) secrets as
**`FUZEINFRA_DISPATCH_TOKEN`**:

```bash
gh secret set FUZEINFRA_DISPATCH_TOKEN --repo izzywdev/FuzeFront
```

> **Izzy:** create the token (Option A or B) and add it to FuzeFront's repo
> secrets. The handler in FuzeInfra uses FuzeInfra's *own* CI secrets for the
> Contabo/k3s/state credentials — the dispatch token only authorizes the
> *trigger*, never the apply.

### Consumer side — example `infra-dispatch.yml`

The consumer's workflow fires the dispatch (for reference; lives in the consumer repo):

```yaml
name: infra-dispatch
on:
  push:
    branches: [main]
    paths: ["deploy/terraform/**", "deploy/argocd/**"]
jobs:
  dispatch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Dispatch infra-request to FuzeInfra
        env:
          GH_TOKEN: ${{ secrets.FUZEINFRA_DISPATCH_TOKEN }}
        run: |
          # Build the requests[] payload from deploy/terraform (e.g. a committed
          # requests.json) and dispatch it.
          gh api repos/izzywdev/FuzeInfra/dispatches \
            -f event_type=infra-request \
            -F client_payload[repo]="${{ github.repository }}" \
            -F client_payload[ref]="${{ github.sha }}" \
            --input deploy/terraform/requests.json
```

## Required FuzeInfra CI secrets (handler side)

| Secret | Purpose |
|--------|---------|
| `CONTABO_CLIENT_ID` / `CONTABO_CLIENT_SECRET` / `CONTABO_API_USER` / `CONTABO_API_PASSWORD` | Contabo API |
| `K3S_SERVER_URL` / `K3S_NODE_TOKEN` | k3s agent join |
| `CONTABO_IMAGE_ID` | OS image for new nodes |
| `NODE_SSH_PUBLIC_KEY` | Break-glass SSH key injected via cloud-init |
| `KUBE_CONFIG` | base64 kubeconfig — node labeling + Argo sync |
| `TF_STATE_BUCKET` / `TF_STATE_REGION` / `TF_STATE_ACCESS_KEY_ID` / `TF_STATE_SECRET_ACCESS_KEY` | FuzeInfra-owned remote TF state backend |

## Related docs

- [`TERRAFORM_CD.md`](TERRAFORM_CD.md) — the full merge-to-apply model (FuzeInfra-owned plane A + this dispatch bridge plane B), backend bootstrap, and the FuzeOne consumer-side workflows.
- [`K3S_SECOND_NODE_RUNBOOK.md`](K3S_SECOND_NODE_RUNBOOK.md) — manual node-join procedure this automates.
- [`gitops.md`](gitops.md) — Argo CD delivery model.
- [`CONTRACT.md`](../CONTRACT.md) — FuzeInfra's stable service interface.
