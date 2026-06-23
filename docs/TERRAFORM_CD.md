# Terraform CD — merge-to-apply for `terraform/contabo`

Infrastructure that lives **outside** the cluster (Contabo nodes, Cloudflare
DNS/tunnel/Access) needs an explicit `terraform apply` with FuzeInfra's creds and
state. This pipeline makes that apply happen by **merging a reviewed PR** — the
PR review *is* the apply approval. There is no separate post-merge gate.

> The in-cluster plane (Deployments/Services/Jobs) is already pull-reconciled by
> Argo CD and is **not** covered here.

## The model

```
PR touches terraform/**        ──▶  terraform plan -out=tfplan
                                     ├─ posted as a PR comment (visible diff)
                                     └─ uploaded as artifact  tfplan-<headSHA>
        (review + approve = approval to apply)
                                          │
        merge to main  ──────────────────▶  recover the PR head SHA
                                             download THAT exact tfplan
                                             terraform apply tfplan   ◀── saved plan, no blind re-plan
```

- **Apply applies the exact reviewed plan.** The merge job downloads the
  `tfplan` artifact produced by the PR's plan run (keyed by the PR head SHA) and
  runs `terraform apply tfplan`. It never re-plans.
- **Drift fails loud.** If state changed between review and merge, Terraform
  rejects the stale saved plan and the apply step fails — it does not silently
  apply something nobody reviewed.
- **Apply only on push to `main`.** Fork PRs never receive secrets and never
  apply.

Workflow: [`docs/workflows/terraform-contabo-cd.yml`](workflows/terraform-contabo-cd.yml)
(ships in `docs/workflows/` because the authoring bot cannot write
`.github/workflows/`; activate with the `git mv` below).

## Deadlock-safe required check

The `plan` job is the required status check, and it follows the
**always-run + internal-paths-filter** pattern from the companion deadlock-fix:

- The workflow has **no top-level `paths:` filter**, so the `plan` job runs on
  **every** PR to `main` and always reports a conclusion.
- The `terraform/**` filter is applied **inside** the job (`dorny/paths-filter`).
  A PR that doesn't touch terraform succeeds immediately ("nothing to plan").

So a non-terraform PR never blocks on a check that was *skipped* (skipped
required checks sit "pending" forever). Branch protection can safely require
`terraform-contabo-cd / plan`.

## One-time backend bootstrap (S3 + DynamoDB)

`terraform/contabo/backend.tf` is a **partial** S3 backend — no secrets in git;
bucket/table/region are supplied at `init`. Create the state bucket + lock table
once (an account/region you control):

```bash
export AWS_REGION=us-east-1
export TF_STATE_BUCKET=fuzeinfra-tfstate-<unique>
export TF_STATE_LOCK_TABLE=fuzeinfra-tflock

aws s3api create-bucket --bucket "$TF_STATE_BUCKET" --region "$AWS_REGION"
aws s3api put-bucket-versioning --bucket "$TF_STATE_BUCKET" \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$TF_STATE_BUCKET" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws dynamodb create-table --table-name "$TF_STATE_LOCK_TABLE" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region "$AWS_REGION"
```

Migrate any existing local state into the remote backend (run once locally):

```bash
cd terraform/contabo
terraform init -migrate-state \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="key=fuzeinfra/contabo/terraform.tfstate" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
```

> HCP Terraform / Atlantis are the durable upgrade path (native plan-on-PR +
> apply-the-saved-plan). This GHA pipeline meets the acceptance criteria with no
> new infrastructure; swapping the backend block for `cloud {}` and dropping the
> apply job is a clean migration later.

## Required secrets and variables (FuzeInfra repo)

**Variables** (Settings → Secrets and variables → Actions → *Variables*):

| Variable | Example | Purpose |
|---|---|---|
| `TF_STATE_BUCKET` | `fuzeinfra-tfstate-abc` | remote state bucket |
| `TF_STATE_REGION` | `us-east-1` | bucket + lock-table region |
| `TF_STATE_LOCK_TABLE` | `fuzeinfra-tflock` | DynamoDB lock table |

**Secrets** (least-privilege):

| Secret | Scope |
|---|---|
| `TF_STATE_ACCESS_KEY_ID` / `TF_STATE_SECRET_ACCESS_KEY` | IAM user limited to the state bucket + lock table only |
| `CLOUDFLARE_API_TOKEN` | **scoped to the `fuzefront.com` zone**: DNS:Edit, Cloudflare Tunnel:Edit, Access (Apps & Policies):Edit |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID` | account + zone ids |
| `CONTABO_CLIENT_ID`, `CONTABO_CLIENT_SECRET`, `CONTABO_API_USER`, `CONTABO_API_PASSWORD` | Contabo API creds |
| `CONTABO_IMAGE_ID`, `CONTABO_PRODUCT_ID`, `NODE_SSH_PUBLIC_KEY` | node provisioning inputs |
| `GH_PAT` | repo + secrets write, for the `KUBE_CONFIG`/secret provisioners |

Provider creds are injected as `TF_VAR_*` **only** at plan/apply time — never
committed, never exposed to fork PRs.

## Activate

```bash
git mv docs/workflows/terraform-contabo-cd.yml .github/workflows/
git commit -m "ci: activate terraform-contabo merge-to-apply CD"
git push
```

## Branch protection (`main`)

- Require status check: **`terraform-contabo-cd / plan`**.
- Require **1 code-owner review** (the PR review is the apply approval).
- No separate post-merge approval — merge = apply.

## First run reconciles the pending #46 route

The app/auth public route from #46 is already declared in
`terraform/contabo/cloudflare.tf`:

- `local.public_vanity_hosts = ["app", "auth"]`
- the `dynamic "ingress_rule"` adding `app.fuzefront.com` / `auth.fuzefront.com`
  → Traefik in the tunnel config, and
- `cloudflare_record.vanity` — the proxied CNAMEs.

The **first** CD apply on `main` therefore makes `app.fuzefront.com` live.

**Verify before merge** — the PR plan comment should show additions for:

```
+ cloudflare_record.vanity["app"]
+ cloudflare_record.vanity["auth"]
~ cloudflare_zero_trust_tunnel_cloudflared_config.fuzeinfra[0]   # +2 app/auth ingress_rule
```

**Verify after apply:**

```bash
dig +short app.fuzefront.com   # → proxied Cloudflare CNAME (tunnel), not NXDOMAIN
dig +short auth.fuzefront.com
# app/auth are OUTSIDE the *.prod Access wildcard → public:
curl -sI https://app.fuzefront.com   # 200/redirect to the app, NOT a 302 → cloudflareaccess.com
```

## Consumer repos (FuzeOne standard)

Consumer repos hold **no cloud creds**. They declare out-of-cluster needs under
`deploy/terraform/**` and dispatch to FuzeInfra, which is the sole state+creds
owner and applier. That bridge is handled by the existing
[`infra-request-handler.yml`](workflows/infra-request-handler.yml) /
[`docs/INFRA_REQUEST_DISPATCH.md`](INFRA_REQUEST_DISPATCH.md). This document
covers FuzeInfra's own `terraform/contabo` plane.
