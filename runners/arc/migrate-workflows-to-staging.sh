#!/usr/bin/env bash
# =============================================================================
# migrate-workflows-to-staging.sh
# -----------------------------------------------------------------------------
# Flips `runs-on: ubuntu-latest` -> `runs-on: staging` for the FuzeInfra CI /
# build / delegation workflows, leaving the ARC-management workflows (which must
# rebuild the runners from hosted minutes) untouched. See WORKFLOW-MIGRATION.md
# and issue #236.
#
# The Claude GitHub App can't write .github/workflows/**, so run this locally
# and commit the result with a PAT that has `workflow` scope.
#
#   bash runners/arc/migrate-workflows-to-staging.sh          # critical+high+medium
#   bash runners/arc/migrate-workflows-to-staging.sh --all    # + optional
# =============================================================================
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
WF=".github/workflows"

# critical + high + medium
CORE=(
  claude.yml
  claude-smoke.yml
  claude-auto-pr.yml
  infrastructure-tests.yml
  ci-failure-triage.yml
  auto-merge.yml
  update-ignore-list.yml
  argo-deploy-notify.yml
  grafana-crit-fix.yml
  provision-mendys.yml
  rotate-sealed-secret.yml
)

# optional
OPTIONAL=(
  ca-provider-image.yml
  terraform-plan-apply.yml
  deploy-ec2.yml
  actionlint.yml
)

files=("${CORE[@]}")
if [[ "${1:-}" == "--all" ]]; then
  files+=("${OPTIONAL[@]}")
fi

for f in "${files[@]}"; do
  path="$WF/$f"
  if [[ ! -f "$path" ]]; then
    echo "skip (not found): $path" >&2
    continue
  fi
  if grep -qE '^\s*runs-on:\s*ubuntu-latest\s*$' "$path"; then
    sed -i -E 's/^(\s*)runs-on:\s*ubuntu-latest\s*$/\1runs-on: staging/' "$path"
    echo "migrated: $path"
  else
    echo "no ubuntu-latest job (or already migrated): $path"
  fi
done

echo
echo "Review: git diff $WF"
echo "NEVER migrate: arc-bootstrap, arc-reset, arc-reinstall-scaleset,"
echo "               arc-restart-listener, arc-debug, arc-diagnose, build-runner-image"
