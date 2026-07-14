# agent-templates — Managed-Agents role packs for a self-dispatching SDLC

> ⚠️ **This feature belongs in [FuzeAgent](https://github.com/izzywdev/FuzeAgent), not FuzeInfra.**
> It was prototyped here (richest cluster-capable example) but is application-level agent
> orchestration, not shared infra. Port it to FuzeAgent — see
> [PORTING-TO-FUZEAGENT.md](PORTING-TO-FUZEAGENT.md). The generic role/environment/permission
> *pattern* is propagated up to the FuzeSDLC L0 baseline.

Portable **engineer-role agents** (frontend, backend, qa, devops) built on Anthropic
**Claude Managed Agents**. Each role bundles three things so a coding session never
stalls asking a human to "run this on the cluster" or "do this on GitHub":

1. **Persona** — the existing `.claude/agents/*.md` (git source of truth) becomes the agent `system`.
2. **Environment** — a `POST /v1/environments` sandbox with the role's packages + network reach.
3. **Permissions** — per-tool policies (`always_allow` / `always_ask`) + credential scoping.

A **coordinator** agent routes each request to the role whose environment already holds
the needed access, so work is dispatched rather than bounced back to a human.

> Vendor-semi-agnostic: the `.md` personas keep driving Claude Code and the `@claude`
> GitHub Action. This layer *projects* them into `/v1/agents`; nothing is forked.

## Layout

| Path | What |
|---|---|
| `schema/` | JSON Schemas for `role.json` and environment configs |
| `roles/_base/role.json` | shared guardrail system-prompt, default tools + policies, github MCP |
| `roles/{frontend,backend,qa,devops}/role.json` | per-role overrides (`qa` reuses `test-engineer`) |
| `coordinator/coordinator.json` | routing agent (`multiagent` roster over the 4 roles) |
| `environments/*.json` | `cloud-*` (Anthropic sandbox) + `selfhosted-devops` (our worker) |
| `worker/` | self-hosted worker image + guard shims + k8s deploy |
| `sync/` | project manifests → API, and launch sessions |

## Role → environment → guardrail matrix

| Role | Env | Access | Guardrails |
|---|---|---|---|
| frontend | `cloud-frontend` | github MCP; no cluster | agent toolset `always_allow` |
| backend | `cloud-backend` | github MCP; DB client libs; no cluster | `always_allow` |
| qa (test-engineer) | `cloud-qa` | github MCP; app URLs read-only | `always_allow`; never patch to force green |
| devops | **`selfhosted-devops`** | real kubeconfig + PAT on our worker; reaches private k3s | `bash` **`always_ask`**; guard shims block `kubectl delete\|patch\|edit`, `helm uninstall\|rollback`, `terraform apply\|destroy`; prod-sanity system prompt |

## Prerequisites

- `ANTHROPIC_API_KEY` (org has Managed Agents; beta `managed-agents-2026-04-01`).
- `GITHUB_MCP_URL` — URL of a GitHub MCP server (auth handled at the server); referenced by `roles/_base/role.json`.
- For **devops**: a self-hosted environment key (Console: Environments → the env → *Generate environment key*),
  a host inside our network to run `worker/` (holds a **scoped-RBAC** kubeconfig + fine-scoped PAT), and the image built/pushed.
- `pip install -r sync/requirements.txt` (only `jsonschema`, for `validate.py`).

## Usage

```bash
cd agent-templates

# 1. validate every manifest (schema + persona render) before touching the API
python sync/validate.py

# 2. create the cloud + self-hosted environments (idempotent; writes .state/environment-ids.json)
python sync/sync_environments.py

# 3a. create vault credentials (GitHub MCP auth) — reads ${GITHUB_MCP_TOKEN} from env
GITHUB_MCP_URL=https://api.githubcopilot.com/mcp/ GITHUB_MCP_TOKEN=... python sync/sync_vaults.py

# 3b. create the shared cross-session handoff memory store (idempotent)
python sync/sync_memory.py

# 3c. project the personas into versioned /v1/agents + the coordinator (idempotent)
#     (reads ${GITHUB_MCP_URL} and ${HANDOFF_MCP_URL} for the role mcp_servers)
python sync/sync_agents.py

# 3d. run the handoff MCP server (agent-to-agent spawn/resume/ask + memory) — container in prod
python orchestration/handoff_mcp/server.py          # local; or deploy the image (see orchestration/)

# 4a. run a self-hosted worker on an in-network host (devops role executes here)
docker build -t fuzeinfra/agent-worker:dev worker
ANTHROPIC_ENVIRONMENT_ID=env_... ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-... \
  docker run --rm -e ANTHROPIC_ENVIRONMENT_ID -e ANTHROPIC_ENVIRONMENT_KEY \
  -v "$HOME/.kube:/workspace/.kube:ro" fuzeinfra/agent-worker:dev

# 4b. launch a session
python sync/launch_session.py --role qa --prompt "list the tests you would run"
python sync/launch_session.py --coordinator --prompt "validate and deploy the new Grafana dashboard"
```

`launch_session.py` opens the SSE stream, sends the prompt, prints output, and on an
`always_ask` pause surfaces each blocking tool call for `allow`/`deny` (interactive by
default; `--auto allow|deny` for headless; the `approve()` hook is where a Telegram
round-trip plugs in).

## How this removes the "I don't have access" stall

Access is a property of the **environment**, fixed at definition time — not negotiated
mid-session. Cloud roles hold no cluster/prod credentials; the **devops** role runs on a
**self-hosted worker inside our network** where the kubeconfig, PAT and cloud keys live and
the private k3s is reachable. The **coordinator** classifies each request and delegates to
the role whose environment already has the access. Irreversible/prod actions are gated three
ways: control-plane `always_ask` approval, OS-level guard shims on the worker, and a
prod-sanity system prompt. Scoped-RBAC on the worker's ServiceAccount is the primary control;
the shims are a backstop.

## Propagation

Prototyped here in FuzeInfra. The tree is repo-agnostic and lifts into the **FuzeSDLC** L0
baseline as the canonical source; `governance-nightly` drift-checks each repo's roles against
it. `.fuze/manifest.json` gains a `roles` section mirroring `agents`.

## Notes / to confirm before production

- The `ant` CLI install package in `worker/Dockerfile` (`ANT_CLI_PKG`) is a best guess —
  confirm against the Managed Agents quickstart.
- Environments are **not versioned**; changing an `environments/*.json` config means
  delete+recreate (or rename). `sync_environments.py` is create-if-missing by name.
- Give the devops worker a **scoped-RBAC** kubeconfig, not cluster-admin — the guard shims
  are defense-in-depth, not the primary boundary.
