# Propagation — how these agent-templates spread across the Fuze/Mendys family

FuzeInfra was the **prototype home** (richest, cluster-capable example: 17 roles, 11
environments, exec + persona + GTM tiers, secured handoff MCP on Contabo k3s). The generic
**pattern** now lives in the **FuzeSDLC L0 baseline** and the orchestration **runtime** moves
to **FuzeAgent**. This file records the split and the propagation mechanics.

## The split

| Layer | Canonical home | What |
|---|---|---|
| **Framework / pattern** | **FuzeSDLC** `agent-templates/{schema,roles/_base,sync,providers}` | schemas, genericized `_base`, REST client + manifest loader + validate, provider seam (`anthropic` ref + `openai`/`hermes` stubs + `provision.py`) |
| **Concrete definitions** | **this repo** `agent-templates/{roles,environments,vaults,memory,coordinator}` | the 17 roles / 11 environments / vaults / coordinators — declared in `.fuze/manifest.json` `roles` |
| **Orchestration runtime** | **FuzeAgent** | the deployed handoff MCP server, `relay.py`, the self-hosted worker image + its Contabo/CF deploy — see `PORTING-TO-FUZEAGENT.md` |

FuzeInfra is now a **consumer** of the framework: its `schema/roles/_base/sync/providers`
files are canonical in FuzeSDLC and are reconciled to it (see below); its concrete roles are
its own.

## How updates propagate (CI-time pull, not fan-out)

1. **`governance-sync.yml`** (per-repo caller → FuzeSDLC reusable): on every PR, fetch the
   FuzeSDLC canonical at this repo's `baselineRef` via the read-only **`FUZESDLC_DEPLOY_KEY`**,
   reconcile the framework files (commit-back / fail-with-diff). No PR merges against stale
   policy. Deterministic — the hard per-PR gate.
2. **`governance-nightly.yml`**: the floor — LLM-driven nightly drift sweep that also covers
   the `roles` framework + roster (manifest `roles` vs the actual `roles`/`environments`/
   `coordinator` on disk).
3. **`provision-sync.yml`** (per-repo caller → FuzeSDLC reusable `provision.yml`): on merges
   to `main` touching a definition, reconcile agents/environments/vaults/memory into their
   deployed counterparts (idempotent; never prunes).

## Onboarding another repo

Clone it and run the **incremental** bootstrapper from FuzeSDLC:

```bash
scripts/sdlc-bootstrap.sh --canonical <FuzeSDLC-checkout> --repo . --set-secret
```

It stamps the workflow stack incl. the `governance-sync` caller (ref-pinned to `baselineRef`),
the agent subset, and — if the manifest declares `roles` — the framework + `provision-sync`
caller, and sets `FUZESDLC_DEPLOY_KEY`. Re-running on an already-onboarded repo adds only the
new pieces. `mendys*` repos are ordinary consumers (same path).

## Credentials

One **read-only** key (`FUZESDLC_DEPLOY_KEY`) grants read to FuzeSDLC only — provisioned +
distributed by `scripts/setup-fuzesdlc-deploy-key.sh` (run by a human; it modifies access
controls). No cross-repo write credentials anywhere.

> Tracking: FuzeSDLC #38 (framework lift) / FuzeSDLC PR #41 (framework + governance-sync).
