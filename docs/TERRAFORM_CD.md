# Terraform CD — merge-to-apply (FuzeInfra-owned) + consumer dispatch bridge

Infrastructure upgrades go live by **merging a PR**. The PR review *is* the
apply approval — there is no separate post-merge gate. This is the durable fix
for the friction where HCL can be authored but not applied (#43/#46).

There are **two planes**. Only the Terraform plane is covered here.

| Plane | What it manages | Delivery | Status |
|-------|-----------------|----------|--------|
| **Argo** | in-cluster: Deployments/Services, DB-bootstrap & Kafka-topic Jobs | pull-reconciled by the app-of-apps watching each repo's `deploy/argocd/applications/` | already works — a consumer merge auto-reconciles |
| **Terraform** | out-of-cluster: Contabo nodes, Cloudflare DNS/tunnel/Access, cloud IAM/DBaaS | explicit `apply` with FuzeInfra's creds + state | **this doc** |

---

## A) FuzeInfra's OWN `terraform/` → merge-to-apply

```
PR touching terraform/**            merge to main
        │                                  │
        ▼                                  ▼
 terraform plan -out=tfplan        download the SAVED tfplan
 • posted as a PR comment          terraform apply tfplan
 • uploaded as an artifact         (exactly what was reviewed;
   keyed by PR head SHA             Terraform rejects a stale plan)
 • REQUIRED status check
```

Workflow: [`.github/workflows/terraform-plan-apply.yml`](../.github/workflows/terraform-plan-apply.yml) (active).

Key properties:
- **Apply the reviewed plan, not a fresh one.** The PR job runs
  `terraform plan -out=tfplan` and uploads `tfplan` as an artifact keyed by the
  PR head SHA. On merge, the apply job finds the merged PR, recovers that head
  SHA, downloads that exact `tfplan`, and runs `terraform apply tfplan`. If
  state drifted between review and merge, Terraform refuses the stale saved plan
  — it fails loudly rather than applying something unreviewed.
- **Apply only on push to `main`.** Never on PRs, never on fork PRs (plan jobs
  on forks have no secrets and can't apply).
- **Remote state + locking** via S3 + DynamoDB (see below).
- **Least-privilege creds** injected from CI secrets as `TF_VAR_*` at apply
  time (Cloudflare token scoped to the zone, Contabo creds to the project).

### Activation status

The workflow is **active** at `.github/workflows/terraform-plan-apply.yml` — it
runs `plan` on terraform PRs and `apply` (saved plan) on merge to `main`.

**Not yet a required check.** Do NOT add `terraform-plan / plan` to branch
protection's required checks yet: it triggers on `pull_request: paths:
[terraform/**]`, so a non-terraform PR would leave the check **pending forever**
(merge deadlock). Adopt the deadlock-safe pattern first (always-run job +
internal `terraform/**` self-filter so non-terraform PRs go green), being folded
in from PRs #52/#53. Once that lands:

**Settings → Branches → main → Branch protection**:
- Require the status check **`terraform-plan / plan`**.
- Require at least **1 approving review**.

That combination is what makes "nothing applies without a reviewed plan" true.

### One-time backend bootstrap (S3 + DynamoDB)

State and locking must exist before the first `init`. Create them once (any
account that owns the FuzeInfra state):

```bash
aws s3api create-bucket --bucket fuzeinfra-tfstate --region eu-central-1 \
  --create-bucket-configuration LocationConstraint=eu-central-1
aws s3api put-bucket-versioning --bucket fuzeinfra-tfstate \
  --versioning-configuration Status=Enabled
aws dynamodb create-table --table-name fuzeinfra-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region eu-central-1
```

The backend block is a **partial config** ([`terraform/contabo/backend.tf`](../terraform/contabo/backend.tf),
[`terraform/cloudflare/backend.tf`](../terraform/cloudflare/backend.tf)) — bucket/table/region are
supplied at `init` time, nothing secret is in git. Migrate existing local state
once with `terraform init -migrate-state -backend-config=...`.

### Required configuration

**Variables** (Settings → Variables):

| Variable | Default | Purpose |
|----------|---------|---------|
| `TF_ROOT` | `terraform/contabo` | root module the workflow plans/applies |

**Secrets** (Settings → Secrets):

| Secret | Purpose |
|--------|---------|
| `TF_STATE_BUCKET` / `TF_STATE_REGION` / `TF_STATE_LOCK_TABLE` | S3+DynamoDB backend |
| `TF_STATE_ACCESS_KEY_ID` / `TF_STATE_SECRET_ACCESS_KEY` | backend access (state only) |
| `CONTABO_CLIENT_ID` / `CONTABO_CLIENT_SECRET` / `CONTABO_API_USER` / `CONTABO_API_PASSWORD` | Contabo provider |
| `CONTABO_IMAGE_ID` / `CONTABO_PRODUCT_ID` / `NODE_SSH_PUBLIC_KEY` | VPS inputs |
| `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_ZONE_ID` | Cloudflare (zone-scoped token) |
| `GH_TF_TOKEN` | token Terraform uses to set repo secrets (e.g. `KUBE_CONFIG`) |

---

## B) Consumer-repo Terraform → FuzeInfra applies (dispatch bridge)

Consumer repos declare out-of-cluster needs in their own `deploy/terraform/**`
but hold **no cloud creds**. FuzeInfra is the single state + creds owner and is
**always** the applier.

```
consumer merge (deploy/terraform/**)          FuzeInfra
        │                                          │
        ▼  repository_dispatch                     ▼
 infra-dispatch.yml ───────────────►  infra-request-handler.yml
 (scoped dispatch token)              • validate vs whitelist
                                      • whitelisted → terraform apply
                                      • else → gated PR carrying the plan
```

This plane already exists — see [`INFRA_REQUEST_DISPATCH.md`](INFRA_REQUEST_DISPATCH.md)
for the handler, the auto-apply whitelist, and the scoped dispatch token. This
issue adds the **consumer-side FuzeOne standard** workflows:

- [`docs/workflows/consumer/terraform-plan.yml`](workflows/consumer/terraform-plan.yml)
  — plan-on-PR (fmt + validate the declaration; the authoritative cloud plan is
  produced FuzeInfra-side).
- [`docs/workflows/consumer/infra-dispatch.yml`](workflows/consumer/infra-dispatch.yml)
  — dispatch-on-merge.

> **Pinned-module option.** Instead of FuzeInfra checking out the consumer's
> `deploy/terraform`, the FuzeInfra root can reference it as a SHA-pinned
> module: `source = "github.com/<repo>//deploy/terraform?ref=<sha>"`. The
> dispatch already carries `ref` (the merge SHA) for exactly this. Pin to the
> dispatched SHA so the applied module matches what the consumer reviewed.

---

## Make it the FuzeOne standard

Any repo with a `deploy/terraform/` directory ships:

| Side | Workflow | Trigger | Action |
|------|----------|---------|--------|
| **FuzeInfra** | `terraform-plan-apply.yml` | PR / push to main on `terraform/**` | plan-on-PR + apply-saved-plan-on-merge |
| **Consumer** | `consumer/terraform-plan.yml` | PR on `deploy/terraform/**` | fmt + validate the declaration |
| **Consumer** | `consumer/infra-dispatch.yml` | merge on `deploy/terraform/**` | dispatch to FuzeInfra |

FuzeInfra always holds state + creds and is always the applier.

---

## Why GHA here, and the recommended upgrade

The issue prefers **HCP Terraform (Terraform Cloud)** or **Atlantis** over
hand-rolled GHA — both give plan-on-PR + apply-the-reviewed-plan natively, with
managed state/locking and a clearer audit trail. This repo ships a GHA
implementation as a **pragmatic, no-new-infra bridge** that satisfies the
acceptance criteria today (saved-plan apply, remote state + locking, scoped
creds, apply-only-on-merge).

To upgrade later with minimal churn:
- **HCP Terraform:** replace the `backend "s3"` blocks with `cloud {}`, connect
  the workspace to this repo (VCS-driven runs give plan-on-PR + apply-on-merge
  natively), move the `TF_VAR_*` secrets into workspace variables, and retire
  `terraform-plan-apply.yml`. Plane B's handler can `terraform apply` against
  the same workspace.
- **Atlantis:** run the Atlantis server with the FuzeInfra creds, point its
  webhook at this repo, and let `atlantis plan` / `atlantis apply` (on merge)
  replace the GHA jobs.

Either way the model is unchanged: **the PR review is the apply approval, and
the reviewed plan is what applies.**

## Related docs

- [`INFRA_REQUEST_DISPATCH.md`](INFRA_REQUEST_DISPATCH.md) — consumer → FuzeInfra dispatch + whitelist.
- [`gitops.md`](gitops.md) — the Argo CD delivery plane.
- [`K3S_SECOND_NODE_RUNBOOK.md`](K3S_SECOND_NODE_RUNBOOK.md) — manual node-join this automates.
