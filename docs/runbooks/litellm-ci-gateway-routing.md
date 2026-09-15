# Runbook — route hosted-runner CI through the public LiteLLM gateway

## What this is

`fuze.yml` (and the other `fuze-code-action` call sites) run on GitHub-hosted
runners. Those runners cannot resolve the in-cluster gateway DNS name
(`litellm.fuzeinfra.svc.cluster.local`), so `./.github/actions/llm-endpoint`
probes it, fails, and falls back to a **direct vendor key**. When that vendor is
out of credit the agent run fails — the incident this whole path exists to end.

The gateway is also reachable publicly at `https://litellm.prod.fuzefront.com`,
behind **Cloudflare Access**. This runbook turns on routing a hosted runner
through that public host using a Cloudflare Access **service token**, so CI uses
the gateway (with its cross-provider failover) instead of a single vendor key.

## What is already in place (code + infra — no action needed)

- **Service token + policy** — terraform
  `cloudflare_zero_trust_access_service_token.litellm_ci`, bound to the
  `litellm_service` Access app (`litellm.prod.fuzefront.com`) by the
  `litellm_service_ci_token` policy (`terraform/contabo/cloudflare.tf`). It sits
  alongside the human Google/email-OTP policies, so the admin console is
  unchanged.
- **`llm-endpoint` CF-Access support** — the action accepts
  `cf-access-client-id` / `cf-access-client-secret`, sends them on its readiness
  probe, and echoes them back as its `custom-headers` output.
- **`fuze-code-action` wiring** — forwards those inputs to `llm-endpoint` and
  sets the returned headers as `ANTHROPIC_CUSTOM_HEADERS` on the claude rung, so
  the model requests clear Access too (the probe passing is not enough on its
  own — Claude Code v2.1.227+ parses newline-separated `Name: Value`).
- **`fuze.yml`** already forwards `secrets.CF_ACCESS_CLIENT_ID` /
  `secrets.CF_ACCESS_CLIENT_SECRET`. Because `scripts/provision_secrets.py`
  (FuzeSDLC) derives required secrets by scanning `secrets.NAME` references, the
  two names are auto-registered for provisioning the moment the template lands —
  no manifest edit.

With the two secrets **unset**, the `cf-access-client-*` inputs are empty, no CF
headers are sent, and behaviour is byte-identical to before (in-cluster probe →
vendor fallback). Activation is entirely the three human steps below.

## Activation (human steps — deliberately not automated)

The `client_secret` lives only in terraform state (S3 backend). Extract it from a
**local terminal**, pipe it straight into the provisioner's environment, and
never print, paste, or commit it.

1. **Extract the token and provision the two repo secrets** (from
   `terraform/contabo`, then FuzeSDLC — mirrors `outputs.tf`):

   ```bash
   export CF_ACCESS_CLIENT_ID="$(terraform output -raw litellm_ci_service_token_client_id)"
   export CF_ACCESS_CLIENT_SECRET="$(terraform output -raw litellm_ci_service_token_client_secret)"
   # from FuzeSDLC, with the two vars still exported:
   python scripts/provision_secrets.py --owner izzywdev --apply
   ```

   This seals `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` to each repo that
   installs `fuze.yml`. The plaintext never leaves the process.

2. **Point CI at the public gateway** — set the repo (or org) Actions **variable**
   `FUZE_LITELLM_BASE_URL` to `https://litellm.prod.fuzefront.com`.

   > Do **not** do step 2 without step 1. With the secrets unset, the probe would
   > 302 to the Access login page and still fall back — a config that looks
   > deliberate but is worse than leaving the var unset.

3. **Verify** — trigger a `@fuze` run (or re-run any `fuze-code-action` job) and
   read its `llm-endpoint` notice: it should report `mode=litellm vendor=litellm`
   routing through `https://litellm.prod.fuzefront.com`, not a
   `fallback-*` mode. `.github/workflows/litellm-check-keys.yml`
   (`workflow_dispatch`) is a quick independent gateway reachability check.

## Rollback

Unset `FUZE_LITELLM_BASE_URL` (reverts to the in-cluster default → vendor
fallback). The secrets can stay; they are inert without the var. To fully revoke,
delete the token in terraform (`litellm_ci`) and re-apply.
