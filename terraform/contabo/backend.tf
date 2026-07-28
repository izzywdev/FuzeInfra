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

# ---------------------------------------------------------------------------
# STALE-PLAN RECOVERY (why a comment-only commit sometimes lands here)
#
# terraform-plan-apply is merge-to-apply: the plan is computed on the PR, saved
# as an artifact keyed by the PR head SHA, and the merge applies THAT EXACT plan.
# Terraform refuses a saved plan whose state serial moved since it was created.
#
# That refusal is correct and deliberate — it is what stops an unreviewed change
# from applying. But it means any terraform PR whose plan predates ANOTHER
# terraform apply becomes permanently un-appliable: merging two terraform PRs
# close together guarantees the second fails, re-running the job re-downloads the
# same stale artifact, and there is no workflow_dispatch. The config sits merged
# on main and never applies, which is easy to miss because the PR reads as done.
#
# Recovery: land a trivial change under terraform/** (this comment). That runs a
# FRESH plan against current state — which necessarily includes every accumulated
# unapplied change — so one review reconciles the whole backlog. Prior art:
# "chore(tf): replan CI runner node (stale plan refresh)", 2026-07-08.
#
# Review that plan as the apply approval, exactly as normal: it is not a no-op,
# it is the entire pending delta. Safe to rewrite this block on the next refresh.
# ---------------------------------------------------------------------------
