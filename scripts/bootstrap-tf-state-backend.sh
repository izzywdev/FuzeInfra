#!/usr/bin/env bash
# ===========================================================================
# bootstrap-tf-state-backend.sh
# ===========================================================================
# One-time, IDEMPOTENT bootstrap of the Terraform remote-state backend used by
# the merge-to-apply Terraform CD (.github/workflows/terraform-plan-apply.yml).
#
# Creates, in the current AWS account:
#   * S3 bucket  (state store)     — versioned, SSE-encrypted, public access fully
#                                    blocked, TLS-only bucket policy.
#   * DynamoDB table (state locks) — on-demand billing, hash key LockID (S).
#
# It is safe to re-run: every step checks for existence first and is a no-op if
# the resource already exists. It NEVER touches state objects or other buckets.
#
# This is the canonical "ScriptOps" artifact for the backend bootstrap — review
# it in a PR, then a maintainer runs it once with AWS credentials for the target
# account:
#
#   AWS_PROFILE=... ./scripts/bootstrap-tf-state-backend.sh
#
# After it succeeds, migrate the existing local state (see docs/TERRAFORM_CD.md):
#   cd terraform/contabo
#   terraform init -migrate-state \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="key=fuzeinfra/contabo/terraform.tfstate" \
#     -backend-config="region=$TF_STATE_REGION" \
#     -backend-config="dynamodb_table=$TF_STATE_LOCK_TABLE"
#   terraform plan   # MUST be ~no changes before trusting CI to apply
# ===========================================================================
set -euo pipefail

TF_STATE_BUCKET="${TF_STATE_BUCKET:-fuzefront-terraform-state}"
TF_STATE_REGION="${TF_STATE_REGION:-us-east-1}"
TF_STATE_LOCK_TABLE="${TF_STATE_LOCK_TABLE:-fuzefront-terraform-locks}"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
say "AWS account: ${ACCOUNT} · region: ${TF_STATE_REGION}"
say "Bucket: ${TF_STATE_BUCKET} · Lock table: ${TF_STATE_LOCK_TABLE}"

# --- S3 bucket -------------------------------------------------------------
if aws s3api head-bucket --bucket "${TF_STATE_BUCKET}" 2>/dev/null; then
  say "Bucket already exists — skipping create."
else
  say "Creating bucket ${TF_STATE_BUCKET} ..."
  # us-east-1 must NOT pass a LocationConstraint; every other region must.
  if [ "${TF_STATE_REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${TF_STATE_BUCKET}" --region us-east-1
  else
    aws s3api create-bucket --bucket "${TF_STATE_BUCKET}" --region "${TF_STATE_REGION}" \
      --create-bucket-configuration "LocationConstraint=${TF_STATE_REGION}"
  fi
fi

say "Enforcing versioning (per-object rollback) ..."
aws s3api put-bucket-versioning --bucket "${TF_STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

say "Enforcing default SSE (AES256) ..."
aws s3api put-bucket-encryption --bucket "${TF_STATE_BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

say "Blocking ALL public access ..."
aws s3api put-public-access-block --bucket "${TF_STATE_BUCKET}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

say "Applying TLS-only bucket policy ..."
aws s3api put-bucket-policy --bucket "${TF_STATE_BUCKET}" --policy "$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyInsecureTransport",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${TF_STATE_BUCKET}",
        "arn:aws:s3:::${TF_STATE_BUCKET}/*"
      ],
      "Condition": { "Bool": { "aws:SecureTransport": "false" } }
    }
  ]
}
JSON
)"

# --- DynamoDB lock table ---------------------------------------------------
if aws dynamodb describe-table --table-name "${TF_STATE_LOCK_TABLE}" >/dev/null 2>&1; then
  say "Lock table already exists — skipping create."
else
  say "Creating DynamoDB lock table ${TF_STATE_LOCK_TABLE} (on-demand) ..."
  aws dynamodb create-table \
    --table-name "${TF_STATE_LOCK_TABLE}" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  say "Waiting for table to become ACTIVE ..."
  aws dynamodb wait table-exists --table-name "${TF_STATE_LOCK_TABLE}"
fi

say "Done. Backend ready:"
printf '  TF_STATE_BUCKET=%s\n  TF_STATE_REGION=%s\n  TF_STATE_LOCK_TABLE=%s\n' \
  "${TF_STATE_BUCKET}" "${TF_STATE_REGION}" "${TF_STATE_LOCK_TABLE}"
