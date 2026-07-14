# ⚠️ This orchestration feature belongs in FuzeAgent — port it there

**Status: prototyped in FuzeInfra, destined for [FuzeAgent](https://github.com/izzywdev/FuzeAgent).**

## Why it's here (and why that's temporary)

FuzeInfra is the **shared, containerized infrastructure platform — generic infra only,
no application code** (see `CLAUDE.md`). The agent-orchestration capability in this
`agent-templates/` tree — Managed-Agents role definitions, environments, vault/memory
wiring, the handoff MCP server, the relay, and the session launcher — is **application-
level agent product logic**, not shared infrastructure. It was prototyped here because
this repo already had the richest working example (the `claude.yml` cluster-capable
responder with k8s/helm/terraform guard-shims), which made it the fastest place to
build and verify against a real cluster.

Long-term it does **not** belong in FuzeInfra. It belongs in **FuzeAgent**, the agent
product (which already has its own Argo Application: `argocd/applications/fuzeagent.yaml`).

## What to port to FuzeAgent

The whole `agent-templates/` framework and its runtime:

- **Role framework** — `schema/`, `roles/`, `environments/`, `coordinator/` (the
  technical-role × domain matrix + cross-product feature specialists; see
  `docs/fuzeone-agent-map.html`).
- **Credentials & memory** — `vaults/` (MCP auth via vault credentials + managed
  rotation) and `memory/` (the shared cross-session handoff memory store).
- **Orchestration** — `orchestration/` (the `handoff_mcp/` server: spawn / resume /
  ask / approve + `memory_read`/`memory_write`; the deterministic `relay.py`;
  session-resume + memory + repo-file handoff, no transcript copying).
- **Sync + launch tooling** — `sync/` (project manifests → `/v1/agents`,
  `/v1/environments`, vaults, memory stores; `launch_session.py`).
- **Deploy** — the `handoff_mcp/` container image + `deploy/` manifests, and the
  self-hosted `worker/` image — deployed under FuzeAgent's Argo Application, not
  FuzeInfra's.

## What stays in FuzeInfra

- The **generic parts** that are truly shared SDLC baseline (the role/environment/
  permission *pattern* itself) are propagated up to the **FuzeSDLC L0 baseline** so any
  repo can consume them — see the FuzeSDLC propagation issue.
- FuzeInfra keeps only what it needs as a *consumer*: its own repo-expert, its
  `.fuze/manifest.json` role declaration, and the `claude.yml` responder.

## Migration notes for the port

- The role personas here read from `.claude/agents/*.md` in **this** repo. In FuzeAgent,
  repoint `persona` paths at FuzeAgent's own agent defs (or vendor the shared set from
  FuzeSDLC).
- The `devops` self-hosted worker holds real cluster/GitHub/cloud credentials — in
  FuzeAgent, wire those via FuzeAgent's SealedSecrets, and keep the self-hosted worker
  inside the network boundary that must reach the private k3s cluster.
- Deploy the `handoff-mcp` Service under FuzeAgent's Argo Application; set each role's
  `HANDOFF_MCP_URL` to that service's in-cluster DNS.
- Nothing about the design is FuzeInfra-specific except the persona set and the k3s/
  Cloudflare gotchas baked into the guard-shims and system prompts.
