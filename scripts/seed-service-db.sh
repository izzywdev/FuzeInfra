#!/usr/bin/env bash
#
# seed-service-db.sh — canonical bootstrap for a per-service Postgres DB on the
# shared FuzeInfra cluster (issue #228).
#
# THE TWO-SECRET PATTERN
#   A service that wants its own Postgres role + database needs TWO secrets:
#     1. fuzeinfra-namespace secret  <service>-db-credentials  (key: password)
#        — read by the service-DB provisioning Job to CREATE the role/database.
#          THIS script produces the SealedSecret for it.
#     2. app-namespace secret        <service>-db-credentials  (DATABASE_URL, …)
#        — the full connection string the app consumes at runtime.
#          The APP repo seals this one (out of scope here); this script just
#          prints the password so you can hand it to that step.
#   Both must encode the SAME password. Provisioning wedges (x∞
#   CreateContainerConfigError) if secret #1 is missing when the service is
#   flipped `enabled: true` in helm/fuzeinfra/values-contabo.yaml — so land the
#   sealed secret and the enable-flag in the SAME pull request.
#
# WHAT THIS DOES
#   - Generates a 32-char alphanumeric password (unless one is passed).
#     Alphanumeric only: shell/SQL metacharacters break the provisioning Job
#     (CLAUDE.md gotcha).
#   - Seals it OFFLINE (via scripts/seal-secret.sh, against the published public
#     cert — no cluster access needed) into
#       deploy/sealed-secrets/<service>-db-credentials.yaml
#     targeting scope fuzeinfra/<service>-db-credentials, key `password`.
#   - Is idempotent: if that manifest already exists it refuses to clobber it
#     (pass --force to reseal, e.g. for a rotation).
#   - Prints the password + the exact next steps.
#
# USAGE
#   ./scripts/seed-service-db.sh <service-name> [<password>] [--force]
#
# EXAMPLES
#   ./scripts/seed-service-db.sh fuzebilling                 # generate a password
#   ./scripts/seed-service-db.sh fuzebilling "$MY_PW"        # use a given password
#   ./scripts/seed-service-db.sh fuzebilling --force         # reseal (rotation)
#
# This script NEVER writes plaintext to disk (password goes to a 0600 tmpfile
# consumed by kubeseal, removed on exit) and only prints it to stdout at the end.
#
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SEAL="${SCRIPT_DIR}/seal-secret.sh"
OUT_DIR="${REPO_ROOT}/deploy/sealed-secrets"

die()  { echo "error: $*" >&2; exit 1; }
note() { echo "$*" >&2; }

# ── Parse args ───────────────────────────────────────────────────────────────
SERVICE=""
PASSWORD=""
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $arg (try --help)" ;;
    *) if [[ -z "$SERVICE" ]]; then SERVICE="$arg"; elif [[ -z "$PASSWORD" ]]; then PASSWORD="$arg"; else die "unexpected extra arg: $arg"; fi ;;
  esac
done

[[ -n "$SERVICE" ]] || die "missing <service-name> (e.g. fuzebilling). Try --help"
# Service names become part of a k8s object name and a Postgres role/db name.
[[ "$SERVICE" =~ ^[a-z][a-z0-9-]*$ ]] || die "service name must be lowercase [a-z0-9-], starting with a letter: '$SERVICE'"
[[ -x "$SEAL" || -f "$SEAL" ]] || die "cannot find seal-secret.sh at $SEAL"

NAME="${SERVICE}-db-credentials"
SCOPE="fuzeinfra/${NAME}"
OUT="${OUT_DIR}/${NAME}.yaml"

mkdir -p "$OUT_DIR"

if [[ -f "$OUT" && "$FORCE" -ne 1 ]]; then
  die "$OUT already exists — this service's fuzeinfra-namespace SealedSecret is already in git. Pass --force to reseal (rotation)."
fi

# ── Password: generate if not provided ───────────────────────────────────────
GENERATED=0
if [[ -z "$PASSWORD" ]]; then
  PASSWORD="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32)"
  GENERATED=1
fi
[[ "${#PASSWORD}" -ge 16 ]] || die "password too short (<16 chars) — refusing"
[[ "$PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || die "password must be alphanumeric only (shell/SQL metacharacters break the provisioning Job)"

# ── Seal (offline, strict scope, password never hits disk in plaintext) ──────
PW_TMP="$(mktemp)"
trap 'rm -f "$PW_TMP"' EXIT
printf '%s' "$PASSWORD" > "$PW_TMP"

if [[ -f "$OUT" ]]; then
  note "resealing (--force): $OUT"
  rm -f "$OUT"   # replace the single `password` key cleanly rather than merge
fi
note "sealing ${SCOPE} → ${OUT#$REPO_ROOT/}"
bash "$SEAL" "$SCOPE" "password=@${PW_TMP}" --out "$OUT"

# ── Report ───────────────────────────────────────────────────────────────────
cat >&2 <<EOF

────────────────────────────────────────────────────────────────────────────
✅ Sealed fuzeinfra-namespace DB password for '${SERVICE}'.
   Manifest: ${OUT#$REPO_ROOT/}   (scope: ${SCOPE}, key: password)
$( [[ "$GENERATED" -eq 1 ]] && echo "   Password was auto-generated (32-char alphanumeric)." )

NEXT STEPS (do all in ONE pull request — never split the secret from the flag):

  1. In the ${SERVICE} repo, seal the app-namespace secret with the SAME
     password (full DATABASE_URL etc.), e.g.:
         ./scripts/seal-db-credentials.sh <password>
     (whatever that repo's equivalent is) and commit it there / dispatch it via
     the argocd-register flow.

  2. In THIS repo, enable the service in helm/fuzeinfra/values-contabo.yaml
     under serviceDatabases:
         - name: ${SERVICE}
           enabled: true
           role: ${SERVICE}_svc
           database: ${SERVICE}
           passwordSecret:
             name: ${NAME}
             key: password

  3. Commit ${OUT#$REPO_ROOT/} + the values change together, open ONE PR, merge.
     ArgoCD then: applies the SealedSecret (fuzeinfra-sealed-secrets app) →
     controller decrypts it → the provisioning Job creates role '${SERVICE}_svc'
     and database '${SERVICE}'.

The password (hand it to step 1):
EOF
printf '%s\n' "$PASSWORD"
