# ---------------------------------------------------------------------------
# Remote state + locking — S3 backend (partial config).
#
# Bucket/region/lock-table are supplied at init time by CI so nothing secret or
# environment-specific lands in git. The state KEY is hardcoded here and is
# DELIBERATELY DIFFERENT from FuzeInfra's own state key so the two clusters
# never share or clobber state:
#
#   terraform init \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="region=$TF_STATE_REGION" \
#     -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
#
# AWS creds come from env (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).
# ---------------------------------------------------------------------------
terraform {
  backend "s3" {
    key     = "mendys/contabo/terraform.tfstate"
    encrypt = true
  }
}
