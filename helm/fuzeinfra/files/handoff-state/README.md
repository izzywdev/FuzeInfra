This directory holds the handoff MCP id-state as ConfigMap source (helm/fuzeinfra/templates/handoff-mcp.yaml renders `handoff-state` from *.json here).

These are NON-secret ids (agent/env/vault/memory), produced by the provision job (agent-templates/providers/provision.py -> sync/.state/*.json). Populate them (download the "managed-agents-state-anthropic" artifact from the Provision / Sync Managed Agents workflow, or run provision locally) and commit them here in the SAME PR that flips handoffMcp.enabled=true and lands deploy/sealed-secrets/handoff-mcp-secret.yaml.

## The current files are `{}` placeholders, and that is a live problem

`agent-ids.json`, `vault-ids.json` and `memory-ids.json` were committed as empty
objects by #587 so the ConfigMap would exist. Empty state does **not** fail
loudly — and that is the trap:

- the container starts normally and the TCP readiness probe passes, so the pod is
  `Running`/`Ready` and every deploy-side signal is green;
- `_roles()` only raises when `agent-ids.json` is *missing*. It is present and
  empty, so it returns `{}` and every `spawn_agent`/`ask_agent` fails at call time
  with `unknown role '<x>'. Known:` (an empty list);
- `_handoff_store_id()` raises `no memory store synced` for every `memory_write` /
  `memory_read`.

So a caller sees per-tool errors from a service that looks healthy from the
cluster's side. Nothing alerts. Treat "the pod is up" and "handoff works" as
completely separate claims.

## Populating them

The ids come from a real Managed Agents API round-trip; they cannot be
hand-written or invented. As of 2026-08-20 the workflow that produces them fails
before it can:

    POST /v1/agents/agent_017w6cZN3avLiA2G9ZHjAuxa -> HTTP 400:
    "Your credit balance is too low to access the Anthropic API."
    (Sync Managed Agents on merge, run 32322608863)

That is an account-billing condition on the key behind `MANAGED_AGENTS_API_KEY`,
not a code or config problem, and no change in this repo can work around it. Once
credit is restored:

1. re-run **Provision Managed Agents** (`provision.yml`, or the on-merge
   `provision-sync.yml`) and confirm it exits 0;
2. download the `managed-agents-state-anthropic` artifact;
3. commit `agent-ids.json` / `vault-ids.json` / `memory-ids.json` from it here.

The pod rolls automatically on that commit: `checksum/handoff-state` hashes the
contents of these files, so a change to them forces a new pod template. (The
sibling `checksum/handoff-secret` covers the *secret* side, which the chart cannot
hash — see deploy/sealed-secrets/handoff-mcp-secret.yaml.template.)
