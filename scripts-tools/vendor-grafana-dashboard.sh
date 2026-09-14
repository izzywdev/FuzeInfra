#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Vendor a community dashboard from grafana.com/grafana/dashboards into this
# repo as GitOps — so importing it is a `git commit`, not a manual click in
# the Grafana UI on every cluster.
#
# WHY THIS IS A SCRIPT AND NOT PRE-VENDORED JSON: grafana.com is outside this
# repo's CI egress allowlist, so the dashboard JSON has to be fetched from a
# machine with normal internet access (a laptop, a runner with broader
# egress) and committed by a human. This script does the fetch + best-effort
# datasource rewrite; you still open the result in Grafana once to confirm it
# actually renders before committing (community dashboards vary a lot in how
# they template their datasource — see the note it prints).
#
# Usage: ./scripts-tools/vendor-grafana-dashboard.sh <dashboard-id> <slug>
#   dashboard-id  Numeric ID from the grafana.com dashboard URL
#                 (grafana.com/grafana/dashboards/<id>-...)
#   slug          Output filename (monitoring/grafana/dashboards/community/<slug>.json)
#
# Example (Node Exporter Full):
#   ./scripts-tools/vendor-grafana-dashboard.sh 1860 node-exporter-full
#
# After running: the Helm chart auto-discovers ANY file under
# dashboards/community/*.json (helm/fuzeinfra/templates/grafana-dashboards.yaml)
# and files it under the "Infrastructure (Community)" Grafana folder — no
# further chart changes needed. Just commit the JSON.
# -----------------------------------------------------------------------------
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <dashboard-id> <slug>" >&2
  exit 1
fi

DASHBOARD_ID="$1"
SLUG="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="$REPO_ROOT/monitoring/grafana/dashboards/community"
OUT_FILE="$OUT_DIR/${SLUG}.json"

command -v curl >/dev/null || { echo "curl not found" >&2; exit 1; }
command -v jq   >/dev/null || { echo "jq not found" >&2; exit 1; }

mkdir -p "$OUT_DIR"

echo "==> Looking up latest revision for dashboard $DASHBOARD_ID"
REVISION="$(curl -sS "https://grafana.com/api/dashboards/${DASHBOARD_ID}/revisions" \
  | jq -r '[.items[].revision] | max')"
[ -n "$REVISION" ] && [ "$REVISION" != "null" ] || {
  echo "could not resolve a revision for dashboard $DASHBOARD_ID" >&2; exit 1; }

echo "==> Downloading revision $REVISION"
RAW="$(curl -sS "https://grafana.com/api/dashboards/${DASHBOARD_ID}/revisions/${REVISION}/download")"

# Best-effort: point every ${DS_*}-style templated datasource input at our
# own `datasource` template variable so the dashboard uses whichever
# Prometheus datasource Grafana resolves by default (uid: fuzeinfra-prometheus
# in this chart), instead of the placeholder the dashboard shipped with.
# This is a heuristic, not a guarantee — some dashboards template Loki or
# multiple datasources separately, and this rewrite will not fix those.
# ALWAYS open the result in Grafana ("New > Import", paste the JSON, or drop
# it in place and let the chart pick it up) and check every panel resolves
# before committing.
echo "$RAW" \
  | jq 'del(.id) | del(.__inputs) | del(.__requires)
        | (.. | objects | select(has("datasource") and (.datasource | type == "string")) | .datasource) |= "${datasource}"' \
  > "$OUT_FILE"

cat <<EOF

Wrote $OUT_FILE

NEXT STEPS (do not skip):
  1. Open it in a running Grafana (Dashboards > New > Import, paste the JSON)
     and confirm every panel resolves a datasource and renders data — this
     script's datasource rewrite is a heuristic, not a guarantee.
  2. If the dashboard hardcodes a job/label name that doesn't match this
     repo's scrape config (e.g. a 'job=~"node-exporter"' regex vs. our
     job_name: node-exporter), fix it now.
  3. helm lint helm/fuzeinfra && helm template ... | kubeconform -ignore-missing-schemas
  4. git add $OUT_FILE && commit.

Source: https://grafana.com/grafana/dashboards/${DASHBOARD_ID}/ (revision ${REVISION})
EOF
