# Alert → agent session: what exists, what is missing, what a human must decide

Status: **proposal, nothing wired.** Written 2026-10-14 after the Prometheus
`ContainerCreating` incident (Longhorn fsck failure, 13h, no alert, Argo sync blocked).

## Where a critical alert ends today

```
Grafana rule (severity=critical)
  -> contact point fuze-autofix -> CF Worker crit-alert-bridge
  -> repository_dispatch (grafana-crit-alert | alertmanager-alert)
  -> grafana-crit-fix.yml / alertmanager-fuze.yml
  -> a GitHub issue (@fuze mention) in the owning repo
  -> the repo's @fuze handler (fuze-cluster.yml: cluster *reads* + PRs only)
```

The chain ends in an issue plus an email. Nothing starts a Claude Code **cloud session**.
The `@fuze` handler is a GitHub Actions job: it can read the cluster and open reconcile
PRs, but it cannot do what the incident needed (diagnose a stuck Longhorn volume, reason
across Argo/Longhorn/Prometheus, and coordinate a repair session).

## What each candidate path can and cannot do (verified in-repo)

| Path | Creates a session? | Wakes an existing one? | Credential | Billing | Status |
|---|---|---|---|---|---|
| `handoff-mcp` `spawn_agent` | yes | n/a | `MANAGED_AGENTS_API_KEY` / `HANDOFF_MCP_TOKEN` | API credit | credit-blocked; `handoff-state/*.json` are `{}` |
| `a2a_gateway` (`gateway.mjs`, `claude -p --cloud <id>`) | **no** | yes (validated 2026-08-27) | Claude.ai **account** `.credentials.json` in `a2a-gateway-secret`; bearer `FUZE_A2A_GATEWAY_TOKEN` | account plan usage | deployed in contabo (`a2aGateway.enabled: true`) |
| Routines API (`create_session`, `create_trigger`, `fire_trigger`) via the `claude-code-remote` MCP server | **yes** | yes | the *account session* of whoever runs it | account plan usage | works from a Claude Code session (CAPABILITY_DELEGATION.md §3, proven 2026-08-30) |
| `@fuze` handler (`fuze-cluster.yml`) | no (Actions job) | n/a | `CLAUDE_CODE_OAUTH_TOKEN`, `KUBE_CONFIG` | plan usage | what handles alerts today |

Findings that matter:

1. **No workflow in this repo holds a credential that can create a cloud session.**
   `CLAUDE_CODE_OAUTH_TOKEN` is used by `claude-code-action` jobs (run an agent *inside*
   the Actions runner), not to create a claude.ai/code session. I found nothing in the
   repo showing it can. It is not assumed to.
2. The Routines API is an MCP tool surface exposed *inside* a Claude Code session. I found
   no documented HTTP endpoint for it, so a GitHub workflow or the Worker has nothing
   to call. **No endpoint is invented here.**
3. The gateway holds an account credential and works, but its only verb is "deliver a
   message to a session id I am given".

## Options

**A. Do nothing new (current PR).** Alerts open issues; a human or `@fuze` picks them up.
This PR makes sure the right alerts *fire* and are not deduplicated away.

**B. Standing responder session + gateway wake (buildable now, no new credential kind).**
A long-lived "alert-responder" cloud session is registered with the gateway. The alert
workflow `POST`s `/send {to: "alert-responder", text: <alert + issue link>}` to wake it.
- Credential: `FUZE_A2A_GATEWAY_TOKEN` as a GitHub Actions secret (name only; provisioned by
  `secret-provision.yml`, never pasted). The gateway's own account credential already exists.
- Cost: plan usage of the gateway's account per wake; unbounded unless rate-limited. Needs a
  cap (e.g. one wake per `(alertname, namespace)` per 4h, which the issue dedup already gives).
- Blast radius: the responder session has whatever its environment grants (git/PR, the
  `cluster-query.yml` read path). It must stay read-plus-PR; prod writes remain under the
  CLAUDE.md GitOps rule and registered Guarded exceptions. The gateway endpoint is open if
  its token is unset, so the token must be set before this is enabled.
- Weakness: one session accumulates context across unrelated incidents, and a dead or
  archived session id silently drops alerts (the gateway wakes archived sessions, but the
  id must be maintained).

**C. Fresh session per alert (the real ask).** Needs a service that can call the Routines
API on an account's behalf, which does not exist here. Two ways to get one, both needing the
owner:
  1. Run a small always-on "dispatcher" Claude Code session that watches a queue (e.g. the
     `alertmanager-autofix`/`grafana-autofix` issues) and calls `create_session` itself.
     No new credential, but it is a session that must stay alive and be paid for.
  2. Confirm with Anthropic whether a supported HTTP API / service credential exists for
     creating Claude Code sessions. Credential name if one exists: to be decided by the
     owner (suggested `CLAUDE_SESSION_SPAWN_TOKEN`); value never stored in the repo, sealed
     offline via `scripts/seal-secret.sh`.
- Cost: plan usage per session, or API billing if the credential is API-based. A flapping
  alert would spawn sessions unbounded without a hard per-alert cooldown and a daily cap.
- Blast radius: each session is an autonomous agent with repo + cluster-read reach, started
  by an unauthenticated-ish signal (alert labels are attacker-influenceable if any
  workload can emit logs matching a Loki rule). Alert text must be passed as data, and the
  session must not inherit prod write capability.

## Human decisions needed

1. Is a standing responder session (B) acceptable, or is a fresh session per alert (C) required?
2. For C: does a supported, credentialed session-creation API exist, and which credential
   and billing account should it use? Until answered, nothing can be wired without inventing it.
3. Cooldown and spend cap per alert and per day.
4. Who owns the responder session's lifecycle (id registration, rotation, archive handling).

Nothing in `helm/` or `.github/` was added for this; no `enabled` flag exists because there is
no credentialed creation path to gate.
