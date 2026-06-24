# ---------------------------------------------------------------------------
# Remote state + locking (HARD PREREQ for merge-to-apply CD)
#
# PARTIAL backend config — supply bucket/table/region at init time:
#
#   terraform init \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="key=fuzeinfra/cloudflare/terraform.tfstate" \
#     -backend-config="region=$TF_STATE_REGION" \
#     -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
#
# See docs/TERRAFORM_CD.md for the one-time backend bootstrap.
# ---------------------------------------------------------------------------
terraform {
  backend "s3" {
    encrypt = true
  }
}
