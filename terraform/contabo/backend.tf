# ---------------------------------------------------------------------------
# Remote state + locking — REQUIRED for merge-to-apply CD.
#
# This is a *partial* backend: no secrets and no environment-specific values
# live in git. The bucket / table / region are supplied at `terraform init`
# time via `-backend-config=...` (the CD workflow injects them from secrets;
# operators pass them on the CLI for the one-time bootstrap/migration).
#
#   terraform init \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="key=fuzeinfra/contabo/terraform.tfstate" \
#     -backend-config="region=$TF_STATE_REGION" \
#     -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
#
# `encrypt = true` is enforced here; `dynamodb_table` gives state locking so two
# applies (or an apply racing a plan) can never corrupt state. See
# docs/TERRAFORM_CD.md for the one-time backend bootstrap commands.
# ---------------------------------------------------------------------------
terraform {
  backend "s3" {
    encrypt = true
    # bucket / key / region / dynamodb_table supplied via -backend-config at init.
  }
}
