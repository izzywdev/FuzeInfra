# ---------------------------------------------------------------------------
# Remote state + locking (HARD PREREQ for merge-to-apply CD)
#
# Uses S3 for state and DynamoDB for locking so concurrent applies (e.g. a
# merge-to-apply run and an infra-request dispatch) can never corrupt state.
#
# This is a PARTIAL backend config on purpose — no bucket/table/region is
# hardcoded here. CI (and humans) supply them at init time so the same root
# works across environments and nothing secret lands in git:
#
#   terraform init \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="key=fuzeinfra/contabo/terraform.tfstate" \
#     -backend-config="region=$TF_STATE_REGION" \
#     -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
#
# AWS creds come from env (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) — see the
# terraform-plan-apply workflow. For local runs, export the same vars.
#
# To bootstrap the backend once (bucket + lock table), see docs/TERRAFORM_CD.md.
# ---------------------------------------------------------------------------
terraform {
  backend "s3" {
    encrypt = true
  }
}

# CD plan verification trigger — no infra change (see PR). Safe to remove.
