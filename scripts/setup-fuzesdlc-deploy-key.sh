#!/usr/bin/env bash
# Provision the ONE read-only FuzeSDLC deploy key and distribute it to consuming repos.
#
# ⚠️  RUN THIS YOURSELF — it modifies access controls (registers a deploy key on FuzeSDLC)
#     and sets repo secrets. It is intentionally NOT run by automation.
#
# Trust model (min-privilege, max-decoupling): a SINGLE read-only deploy key grants read to
# izzywdev/FuzeSDLC ONLY. Its private half is distributed as the `FUZESDLC_DEPLOY_KEY` secret
# to each consuming repo, whose governance-sync.yml uses it to fetch the FuzeSDLC canonical.
# No repo ever gets write to another; each repo reconciles with its OWN GITHUB_TOKEN + this
# shared read key. FuzeSDLC never needs write to any consumer (no fan-out).
#
# Prereqs: `gh` authenticated as an owner/admin of izzywdev/FuzeSDLC and the target repos;
# `ssh-keygen` available. Also enable, once, in FuzeSDLC settings → Actions → General →
# "Access": *Allow repositories owned by izzywdev to use this repository's workflows* (so the
# consumers' `uses: izzywdev/FuzeSDLC/...` resolves for a private FuzeSDLC).
#
# Usage:
#   scripts/setup-fuzesdlc-deploy-key.sh <repo> [<repo> ...]
#   e.g. scripts/setup-fuzesdlc-deploy-key.sh izzywdev/FuzeInfra izzywdev/FuzePicker
set -euo pipefail

SDLC_REPO="izzywdev/FuzeSDLC"
[ $# -ge 1 ] || { echo "usage: $0 <owner/repo> [<owner/repo> ...]"; exit 2; }

command -v gh >/dev/null || { echo "gh CLI required"; exit 1; }
command -v ssh-keygen >/dev/null || { echo "ssh-keygen required"; exit 1; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
key="$tmp/fuzesdlc_ro"
ssh-keygen -t ed25519 -N "" -C "fuzesdlc-governance-sync-ro" -f "$key" >/dev/null
echo ">> generated ephemeral ed25519 keypair (private half never leaves this run except as repo secrets)"

# 1) Register the PUBLIC half as a READ-ONLY deploy key on FuzeSDLC.
if gh api "repos/$SDLC_REPO/keys" --jq '.[].title' 2>/dev/null | grep -qx "governance-sync-ro"; then
  echo ">> a 'governance-sync-ro' deploy key already exists on $SDLC_REPO."
  echo "   To rotate: delete it in FuzeSDLC → Settings → Deploy keys, then re-run this script."
else
  gh api "repos/$SDLC_REPO/keys" -f title="governance-sync-ro" -f key="$(cat "$key.pub")" -F read_only=true >/dev/null
  echo ">> registered READ-ONLY deploy key 'governance-sync-ro' on $SDLC_REPO"
fi

# 2) Distribute the PRIVATE half as FUZESDLC_DEPLOY_KEY to each consuming repo.
for repo in "$@"; do
  gh secret set FUZESDLC_DEPLOY_KEY -R "$repo" < "$key"
  echo ">> set FUZESDLC_DEPLOY_KEY on $repo"
done

echo ">> DONE. Governance-sync on the target repos can now fetch the FuzeSDLC canonical."
echo "   Verify (min-privilege): the key reads $SDLC_REPO only — it cannot write, and cannot read another repo."
