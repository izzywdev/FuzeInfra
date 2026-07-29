# Desktop / web cloud environments — the picker projection

This directory holds the **Claude Code desktop + web cloud environment** configuration for the
`Fuze` and `DevOps` environments, rendered from the environment JSONs one level up.

## Read this first: these are two different systems

The environment picker in the Claude Code desktop app (the dropdown under the task box, "Cloud"
section → **Add cloud environment**) is **not** the Managed-Agents `/v1/environments` API. They
share a word and nothing else.

|  | Managed-Agents environment | Desktop / web cloud environment |
|---|---|---|
| Resource | `POST /v1/environments` on `api.anthropic.com` | a claude.ai **account** resource — **no public API** |
| Auth / scope | API key, billed to an API **organization** | your claude.ai login; "personal to your account" |
| Config fields | `config.type`, `config.packages.{apt,cargo,gem,go,npm,pip}`, `config.networking.{type,allowed_hosts,allow_mcp_servers,allow_package_managers}` | **Name**, **Network access** (None / Trusted / Full / Custom + allowed domains), **Environment variables** (`.env`), **Setup script** (Bash) |
| Created by | [`../../providers/provision.py`](../../providers/provision.py) | **only** the dialog at [claude.ai/code](https://claude.ai/code) — or admin-settings for org-shared ones |
| Removal | archive **or** `DELETE` | archive only — "You can't delete an environment, only archive it" |

There is no `packages` field and no setup-script field on the API side, and no API at all on the
picker side. So **`POST /v1/environments` cannot put anything in the desktop picker** — that was
the tempting wrong assumption this directory exists to prevent someone repeating.

Sources: [Configure cloud environments](https://code.claude.com/docs/en/cloud-environments) ·
[Managed Agents environments](https://platform.claude.com/docs/en/managed-agents/environments).

## One source of truth, two consumers

```
../cloud-fuze.json    ─┬─►  providers/provision.py  ──►  POST /v1/environments   (Managed Agents)
../cloud-devops.json  ─┘
                       └─►  render.py               ──►  {fuze,devops}.setup.sh
                                                         {fuze,devops}.domains.txt
                                                         {fuze,devops}.env        (paste at claude.ai/code)
```

`config.packages` is the single declaration. Add a package there and re-run the renderer; the two
systems cannot drift.

```bash
python render.py            # regenerate
python render.py --check    # exit 1 if the committed output is stale (CI-friendly)
```

The generated files are committed deliberately: the picker has no API, so a human (or a browser
session) has to paste them, and reviewing the diff of what gets pasted is the only review there is.

## Registering them in the picker

The picker's environments can only be created through the UI:

1. Go to [claude.ai/code](https://claude.ai/code) and click the cloud icon in the row above the
   message box. (There is no settings page and no direct URL for the selector.)
2. **Add cloud environment**, then fill the dialog from the files here:

| Dialog field | Fuze | DevOps |
|---|---|---|
| Name | `Fuze` | `DevOps` |
| Network access | **Trusted** | **Custom** — hosts from `devops.domains.txt`, **and check** "Also include default list of common package managers" |
| Environment variables | `fuze.env` | `devops.env` |
| Setup script | `fuze.setup.sh` | `devops.setup.sh` |

`/remote-env` in the terminal only *picks* a default for `claude --cloud`; it cannot add or edit
environments.

The first session in a new environment runs the setup script, then Anthropic snapshots the
filesystem and later sessions skip it. Editing the setup script or the allowed hosts rebuilds
that snapshot; so does the roughly seven-day cache expiry.

## The two environments

### Fuze — the general agentic-dev environment

The superset the domain environments build on: `gh`, the shared pytest/httpx/pyyaml test deps,
`yamllint` + `check-jsonschema` for manifest work, `prettier`. Everything else it needs is already
in the base image (git, Python, Node 20–22, Go, Docker, Postgres, Redis, jq, yq, ripgrep).

**The agent roster is not something this environment grants.** Cloud sessions clone the repo, and
`.claude/agents/`, `.claude/skills/`, `.claude/commands/`, `.claude/rules/`, `CLAUDE.md` and
`.mcp.json` all come along with the clone — in *any* environment, including **Default**. What the
environment adds is the toolchain and the network policy, nothing more.

**`sdlc-bootstrap.sh` / governance-sync do not run here**, and that is deliberate. The FuzeSDLC
bootstrapper lives in the FuzeSDLC repo (see [`../../PROPAGATION.md`](../../PROPAGATION.md)) and
reconciliation needs the read-only `FUZESDLC_DEPLOY_KEY`. A cloud environment has **nowhere safe to
hold that key** — see *Secrets* below — so governance-sync stays in CI, where the key already
lives. This environment carries the toolchain the bootstrapper needs; it does not carry the
credential.

### DevOps — the GitOps slice

Mirrors [`../../roles/devops/role.json`](../../roles/devops/role.json) and the
`devops-engineer` agent, for the manifest-editing half of the job: `helm`, `kubeconform`,
`kubeseal`, `yq`/`jq`, `gh`, `git`. Scope is **edit Helm / Argo / values → lint + validate → open a
PR**.

> **Boundary — no prod cluster access, on purpose.**
> This environment gets **no kubeconfig and no cluster credentials**. Direct `kubectl` against the
> Contabo k3s prod cluster stays on the existing **`fuzeinfra-selfhosted-devops`** worker, which
> runs inside our network with the guard-shims that block irreversible verbs. Never put a prod
> kubeconfig in a cloud sandbox: its environment variables are readable by anyone who uses the
> environment, and prod is GitOps anyway — Argo `selfHeal` reverts out-of-band changes within
> seconds, so a hand-applied change from here would be both unsafe and useless.
>
> `FUZE_GITOPS_ONLY=true` is set in `devops.env` as a marker of this boundary.

`kubeseal` here seals secrets against the **published** sealed-secrets certificate, which is
public by design — that needs no cluster access.

## Secrets

Cloud environments have **no secrets store**, and the dialog itself warns that anyone who uses the
environment can read the values. So neither `.env` file contains a credential, and that is the
design, not an omission:

- **`GITHUB_TOKEN` / `GH_TOKEN` — intentionally unset.** All GitHub traffic goes through a proxy
  that keeps real credentials outside the VM; with both unset they read as the literal string
  `proxy-injected` in-session and the proxy substitutes the real token on outbound requests.
  Pasting a PAT would *downgrade* this. (Consequence to know: a script that reads `$GITHUB_TOKEN`
  expecting a usable token gets the placeholder. `gh` itself works fine.)
- **`ANTHROPIC_API_KEY` — not set.** The hosting environment manages the session's API connection.
- **Cloudflare + GitHub MCP — per-session, not per-environment.** There is no MCP field in the
  environment dialog. MCP connector traffic "travels through Anthropic's servers rather than the
  session's network", so connectors need no entry in the allowed-domains list — you enable them
  **per session** on claude.ai. Committing a `.mcp.json` does not help for these two: they are
  remote OAuth servers, and interactive auth cannot complete inside a cloud session.
- **`FUZESDLC_DEPLOY_KEY` — never here.** See the Fuze section above.

## Network access

`Trusted` is the default allowlist (package registries, GitHub, cloud SDKs) and covers Fuze
entirely. DevOps needs `get.helm.sh` for the Helm release tarball, which is why it uses `Custom`
with the defaults still included.

Note the constraint that shaped the setup scripts: the GitHub proxy scopes release-asset requests
to repositories **attached to the session**, so downloading a release from an unattached repo
returns 403. That rules out the usual "curl the GitHub release" install for `kubeconform` and
`kubeseal` — both go through `go install` and `proxy.golang.org` instead, and Helm resolves its
version from `get.helm.sh/helm-latest-version` with a `go install` fallback rather than pinning a
tag that would eventually go stale.
