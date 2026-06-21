#!/usr/bin/env bash
# One-shot: create Cloudflare Access app + email-OTP policy for *.prod.fuzefront.com
#
# Requires a CF API token with:
#   Account > Access: Apps and Policies > Edit
#
# Usage:
#   CF_API_TOKEN=<your-token> bash terraform/contabo/setup-cf-access.sh
set -euo pipefail

ACCOUNT_ID="8c535091cda7f0e7a55ee29a7b1999af"
ALLOWED_EMAIL="izzy.weinberg@gmail.com"
APP_DOMAIN="*.prod.fuzefront.com"

: "${CF_API_TOKEN:?Set CF_API_TOKEN env var before running}"

echo "Creating Cloudflare Access application for ${APP_DOMAIN}..."

APP=$(curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/access/apps" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"FuzeInfra Production Admin Services\",
    \"domain\": \"${APP_DOMAIN}\",
    \"type\": \"self_hosted\",
    \"session_duration\": \"24h\",
    \"auto_redirect_to_identity\": false,
    \"app_launcher_visible\": true
  }")

echo "App response: $(echo $APP | python3 -c 'import sys,json; d=json.load(sys.stdin); print("success:",d["success"],"id:",d.get("result",{}).get("id",""),"errors:",d.get("errors",[]))')"

APP_ID=$(echo $APP | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["id"])')

if [ -z "$APP_ID" ]; then
  echo "ERROR: Failed to create Access application"
  echo "$APP" | python3 -m json.tool
  exit 1
fi

echo "Created app ID: ${APP_ID}"
echo "Creating email OTP policy for ${ALLOWED_EMAIL}..."

POLICY=$(curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/access/apps/${APP_ID}/policies" \
  -H "Authorization: Bearer ${CF_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Admin email allowlist (OTP)\",
    \"decision\": \"allow\",
    \"precedence\": 1,
    \"include\": [{\"email\": {\"email\": \"${ALLOWED_EMAIL}\"}}]
  }")

echo "Policy response: $(echo $POLICY | python3 -c 'import sys,json; d=json.load(sys.stdin); print("success:",d["success"],"id:",d.get("result",{}).get("id",""),"errors:",d.get("errors",[]))')"

echo ""
echo "Done! argocd.prod.fuzefront.com and all *.prod.fuzefront.com"
echo "now require email OTP for ${ALLOWED_EMAIL}."
