#!/usr/bin/env bash
#
# seed-neo4j-instance.sh — bootstrap a per-consumer dedicated Neo4j instance on
# the shared FuzeInfra cluster.
#
# WHY DEDICATED INSTANCES?
#   Neo4j Community edition (what FuzeInfra runs) supports exactly one database
#   and no RBAC. Sharing the shared instance means all consumers share the admin
#   credential and the same single database — no isolation at all. Each consumer
#   therefore gets its own StatefulSet+PVC, giving full isolation with no
#   Enterprise license required (Option B from issue #157).
#
# WHAT THIS DOES
#   - Generates a 32-char alphanumeric password (unless one is passed).
#     Alphanumeric only: special characters can break Neo4j's NEO4J_AUTH parsing.
#   - Seals it OFFLINE (via scripts/seal-secret.sh) into
#       deploy/sealed-secrets/neo4j-<service>-credentials.yaml
#     targeting scope fuzeinfra/neo4j-<service>-credentials, key `password`.
#   - Is idempotent: refuses to overwrite an existing manifest (pass --force to
#     reseal, e.g. for a rotation).
#   - Prints the password + the exact next steps.
#
# USAGE
#   ./scripts/seed-neo4j-instance.sh <service-name> [<password>] [--force]
#
# EXAMPLES
#   ./scripts/seed-neo4j-instance.sh fuzeplan                # generate a password
#   ./scripts/seed-neo4j-instance.sh fuzeplan "$MY_PW"       # use a given password
#   ./scripts/seed-neo4j-instance.sh fuzeplan --force        # reseal (rotation)
#
# After sealing:
#   1. Flip enabled:true in serviceNeo4jInstances.fuzeplan in values-contabo.yaml.
#   2. Open ONE PR committing both the sealed file AND the enabled:true change.
#   3. In the consumer repo, seal the app-namespace secret (NEO4J_URI / NEO4J_USER
#      / NEO4J_PASSWORD) using the SAME password printed at the end of this script.
#
# Bolt address once live:
#   bolt://fuzeinfra-neo4j-<service>.fuzeinfra.svc.cluster.local:7687
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
    -h|--help) sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $arg (try --help)" ;;
    *) if [[ -z "$SERVICE" ]]; then SERVICE="$arg"; elif [[ -z "$PASSWORD" ]]; then PASSWORD="$arg"; else die "unexpected extra arg: $arg"; fi ;;
  esac
done

[[ -n "$SERVICE" ]] || die "missing <service-name> (e.g. fuzeplan). Try --help"
[[ "$SERVICE" =~ ^[a-z][a-z0-9-]*$ ]] || die "service name must be lowercase [a-z0-9-], starting with a letter: '$SERVICE'"
[[ -x "$SEAL" || -f "$SEAL" ]] || die "cannot find seal-secret.sh at $SEAL"

NAME="neo4j-${SERVICE}-credentials"
SCOPE="fuzeinfra/${NAME}"
OUT="${OUT_DIR}/${NAME}.yaml"

mkdir -p "$OUT_DIR"

if [[ -f "$OUT" && "$FORCE" -ne 1 ]]; then
  die "$OUT already exists. Pass --force to reseal (rotation)."
fi

# ── Password: generate if not provided ───────────────────────────────────────
GENERATED=0
if [[ -z "$PASSWORD" ]]; then
  PASSWORD="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32)"
  GENERATED=1
fi
[[ "${#PASSWORD}" -ge 16 ]] || die "password too short (<16 chars) — refusing"
[[ "$PASSWORD" =~ ^[A-Za-z0-9]+$ ]] || die "password must be alphanumeric only"

# ── Seal ─────────────────────────────────────────────────────────────────────
PW_TMP="$(mktemp)"
trap 'rm -f "$PW_TMP"' EXIT
printf '%s' "$PASSWORD" > "$PW_TMP"

if [[ -f "$OUT" ]]; then
  note "resealing (--force): $OUT"
  rm -f "$OUT"
fi
note "sealing ${SCOPE} → ${OUT#$REPO_ROOT/}"
bash "$SEAL" "$SCOPE" "password=@${PW_TMP}" --out "$OUT"

# ── Report ───────────────────────────────────────────────────────────────────
cat >&2 <<EOF

────────────────────────────────────────────────────────────────────────────
✅ Sealed fuzeinfra-namespace Neo4j credentials for '${SERVICE}'.
   Manifest: ${OUT#$REPO_ROOT/}   (scope: ${SCOPE}, key: password)
$( [[ "$GENERATED" -eq 1 ]] && echo "   Password was auto-generated (32-char alphanumeric)." )

NEXT STEPS (do all in ONE pull request — never split the secret from the flag):

  1. In helm/fuzeinfra/values-contabo.yaml, find the serviceNeo4jInstances
     entry with name: ${SERVICE} and flip:
         enabled: false  →  enabled: true

  2. Commit ${OUT#$REPO_ROOT/} + the enabled:true change together, open ONE PR.
     ArgoCD reconciles: applies the SealedSecret → controller decrypts → the
     Neo4j StatefulSet starts with NEO4J_AUTH=neo4j/<password>.

  3. In the ${SERVICE} repo, seal the app-namespace secret with the SAME
     password (printed below). Typical contents:
         NEO4J_URI=bolt://fuzeinfra-neo4j-${SERVICE}.fuzeinfra.svc.cluster.local:7687
         NEO4J_USER=neo4j
         NEO4J_PASSWORD=<password>

  4. In ${SERVICE}/.fuze/manifest.json add to dataTier:
         { "type": "neo4j", "store": "${SERVICE}" }
     so the nightly dataTier reconciler knows the provision should exist.

The password (hand it to step 3):
EOF
printf '%s\n' "$PASSWORD"
