# ---------------------------------------------------------------------------
# Remote state + locking for terraform/contabo (merge-to-apply CD, issue #47).
#
# Partial backend config: bucket / dynamodb_table / region are supplied at
# `terraform init` time via -backend-config (CI uses repo vars TF_STATE_*),
# so nothing secret or environment-specific is committed here.
#
# One-time bootstrap (S3 bucket + DynamoDB table) is documented in
# docs/TERRAFORM_CD.md. DynamoDB provides state locking so concurrent
# applies (CD + a human) can never corrupt state.
# ---------------------------------------------------------------------------
terraform {
  backend "s3" {
    key     = "fuzeinfra/contabo/terraform.tfstate"
    encrypt = true
    # bucket         = supplied via -backend-config at init
    # dynamodb_table = supplied via -backend-config at init
    # region         = supplied via -backend-config at init
  }
}
