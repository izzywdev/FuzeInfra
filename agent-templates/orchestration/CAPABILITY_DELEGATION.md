# Capability delegation across sessions — design + working transport

> **One-line goal.** A session that lacks a capability (e.g. prod `kubectl`, a cloud
> credential, a privileged zone) does **not** get the credential. It asks a session
> that already has it — running in the environment where that credential lives — and
> gets back only the **result**. Chain this A→B→C to solve problems across layers and
> zones without spreading secrets.

This document reconciles the three A2A mechanisms in this repo, picks the transport
that actually works today, and specifies the **fail-closed authorization model** that
makes cross-session capability delegation safe. It is the design gate for the
follow-up implementation PRs (see *Phased plan*).

---

## 0. Why now — the "orphaned agents" symptom

The DevOps (and other) managed agents exist but have gone **unused for months**, with
**no error messages**. That silence is the tell: **nothing ever invokes them.** Three
compounding causes, all addressed by this design:

1. **No caller guidance.** No governance/skill/`CLAUDE.md` rule tells a session *"you
   lack cluster / secret-provisioning access → delegate to a DevOps session in the
   environment that has it."* So a session that hits a capability wall just stops or
   works around it — it never reaches for the agent. **No invocation ⇒ no failure ⇒ no
   message.** (This is exactly the observed symptom.)
2. **The intended path is broken silently.** `spawn_agent`/`ask_agent` (handoff-mcp)
   return "unknown role" because managed-agents provisioning failed on Anthropic credit
   and the role-state files are `{}` placeholders (`.fuze/manifest.json` `mcp.note`). A
   caller that *did* try would fail — but nobody tries (see #1), so even that failure is
   invisible.
3. **The capability may not actually be wired.** Being "a DevOps agent" is not the same
   as *holding the credential*. See §5 — today `cloud-devops` is **GitOps-only** (no
   kubeconfig, `GITHUB_TOKEN` unset), so it can edit manifests and open PRs but cannot,
   e.g., provision GitHub secrets or apply to prod. Delegation guidance is useless if the
   callee can't actually do the thing.

So productionizing is three moves: **(a)** tell callers to delegate (guidance/skill),
**(b)** make the callee reachable (working transport + fixed handoff-mcp), **(c)** make
the callee actually *capable and authorized* (real creds in the right env + fail-closed
grants). All three are in the *Phased plan*.

---

## 1. The model already exists (this is not new)

`orchestration/README.md` §2–§3 already describes the pattern:

```
A: spawn_agent(role="devops", task="<concise>", reply_to_session_id=A.session_id)
   → returns B.session_id; A goes IDLE (no live container while it waits)
B: …does the privileged work in ITS OWN environment (creds scoped there)…
B: resume_session(session_id=A.session_id, summary="<result>")  → A WAKES
```

Two properties make it the right shape for credential isolation:

- **Per-environment credential scoping.** B runs in the environment that owns the
  capability (`cloud-devops`, `selfhosted-devops`, …). The caller never receives the
  secret — only B's summarized result. The handoff MCP even holds `ANTHROPIC_API_KEY`
  server-side so it is never in any sandbox.
- **Only a pointer travels.** The originating `session_id` (+ a short summary or a repo
  /memory pointer) is the whole "context." No transcript is copied; history is restored
  server-side on resume.

Nothing below changes that model. What follows is **which transport carries it**, and
**how the callee decides whether to honor a request.**

---

## 2. Three transports — status

| # | Mechanism | Where | Status |
|---|-----------|-------|--------|
| 1 | **handoff-mcp** (`spawn_agent`/`ask_agent`/`resume_session`) | `orchestration/handoff_mcp/server.py`, deployed via `helm/fuzeinfra/templates/handoff-mcp.yaml` | **Intended, but non-functional in prod.** Managed-agents provisioning fails on Anthropic credit → every call returns "unknown role"; CF Access may 302 machine callers. (See `.fuze/manifest.json` `mcp.note`.) |
| 2 | **WSS relay + gateway + desktop bridge** | `orchestration/a2a_relay/`, `orchestration/a2a_gateway/`, `environments/desktop/a2a-bridge/` | **Deprecated dead-end.** Outbound works, but *inbound* delivery requires writing frames to the peer's internal `CLAUDE_CODE_MESSAGING_SOCKET` (`peerProtocol` v1, compiled into `/opt/claude-code/bin/claude`). That frame format is undocumented and reverse-engineering it is deliberately guardrailed. Do not build on it. |
| 3 | **Routines API** (`create_session` + `create_trigger`/`fire_trigger` + `list_sessions`) | `claude-code-remote` MCP server (platform-native) | **Works — proven end-to-end 2026-08-30.** No relay, no socket, no gateway token, no reverse-engineering. `fire_trigger` even wakes an idle/suspended peer. |

**Decision:** carry the model on **transport #3 today**. Transport #1 (handoff-mcp) is
not just "unfixed" — it is on a **different billing model** that is the actual blocker
(below), so treat it as optional, not the target. **Retire #2.**

### 2a. The two billing models (this is why #3 wins, not just that it works)

| Path | What actually runs | Billed as |
|---|---|---|
| **handoff-mcp `spawn_agent`** (Managed Agents API) | an API-managed agent session | **per-token Anthropic API credit** — the exact thing that is exhausted; this is *why* provisioning fails and the agents are dark |
| **`create_session` / desktop-launched cloud session** (Claude Code) | a Claude Code session in the target environment | **subscription / plan usage** — works today |

Evidence: a `create_session`-spawned peer reports a **`seven_day` rate-limit window** in
its session record — a plan-usage concept, not API metering. So spawning a Claude Code
session (from the desktop app, or via `create_session`) runs on the account's **usage
plan**, independent of the API credit that blocks the managed-agents path. The working
transport is therefore also the **unblocked and cheaper** one.

**Addressing follows from this.** The Claude Code path has **no "agent" object** — you
spawn into an **environment** (`environment_id`, e.g. the `cloud-devops` env), so
delegation keys on **capability → environment** (§5), *never* on a Managed-Agents
`agent_id`. The `role → agent_id` map (`handoff-state/agent-ids.json`) is an artifact of
the API path only; the display name ("FuzeInfra devops-engineer") is not an API handle
there either — the API references agents solely by opaque `agent_id`. **Seeding that map
does not unblock delegation** — it only turns "unknown role" into "insufficient credit."

**The API-path session assembly is already built** (the gap is data + credit, not code).
`handoff_mcp/server.py::_new_session(role)` reads `agent-ids.json`
(`role → {id, version, environment_id}`), attaches `vault-ids.json` (credentials) and
`memory-ids.json` (shared memory), and calls `driver.create_session(...)` to form a
fully-running managed-agents session — **agent + version + environment + creds + memory**.
The `sync/*.py` + `providers/provision.py` scripts create those resources and write the
three id maps; the maps are `{}` because the *Provision Managed Agents* workflow is
credit-blocked, not because the coupling is missing.

### 2b. Which path to use — keyed on where the CALLER runs

| Caller context | Use | Why |
|---|---|---|
| **Local / desktop Claude Code session** | Launch a **Claude Code session in the DevOps environment by name** ("DevOps" in the desktop env picker, or `create_session(environment_id=<cloud-devops>)`) | **Subscription/plan usage** — no API credit, no `agent_id` needed; the assembly is just "spawn into the env" (§3) |
| **Remote / non-local** (a managed-agent or headless/API context, no desktop) | `handoff-mcp spawn_agent("devops", …)` — resolves the **`agent_id`** and assembles agent+env+creds+memory via the Managed Agents API | **API-billed** — needs credit + the id maps populated; this is the built code above |

Both paths carry the *same* delegation envelope and fail-closed authorization (§4); only
the transport and billing differ. Prefer the local/subscription path when the caller has
it; fall back to the managed-agent path when running non-locally.

---

## 3. The working transport (#3) — mapping and proof

The Routines API implements the same `spawn + reply_to_session_id + resume` semantics:

| README semantic | Routines-API call |
|---|---|
| `spawn_agent(role, task, reply_to_session_id)` | `create_session(prompt="[A2A from <A>] <task>", environment_id=<role env>, tags=[…])` |
| `ask_agent` / `resume_session(id, summary)` (deliver a turn, wake if idle) | `create_trigger(persistent_session_id=<id>, prompt="[A2A from <self>] <msg>", initiation=…)` → `fire_trigger(trigger_id)` → `delete_trigger(trigger_id)` |
| addressing / discovery | `session_id` is the address (`cse_<X>` ↔ `session_<X>`); enumerate with `list_sessions(mine:true, tags:[…])` |

**Message envelope (convention).** Every delivered turn starts with a machine-parseable
header so the receiver knows the sender, the correlation id, and where to reply:

```
[A2A from=<sender session_id> corr=<uuid> reply_to=<sender session_id> cap=<capability>] <body>
```

**Reply routing.** The receiver replies by firing a trigger back at `reply_to`, echoing
`corr`. Wake-idle is free (`fire_trigger` runs the target server-side) — so the caller
can go idle after asking and costs nothing until the reply lands.

**Proof.** A ping from session A was delivered into a fresh peer B; B replied with
`create_trigger(persistent_session_id=A)` + `fire_trigger`; A was woken by
`[A2A from B] pong … relay-free round trip via the Routines API worked.` Both sides
logged success. A `create_session`-spawned peer **has** the Routines tools, so no
special provisioning is needed for a peer to participate.

**Honest limits.** Same **account** only (it operates over the caller account's
sessions — cross-*org* still needs a real broker). Latency is seconds (a fired trigger
provisions/wakes a container), fine for coordination, not a low-latency bus. Delivery is
a **new turn** — there is no separate "message vs. turn."

---

## 4. Authorization — fail-closed, or it's privilege escalation

Capability delegation is a **confused-deputy** risk: if any session can tell a DevOps
session "run this against prod," that is privilege escalation with extra steps. The
credential isolation is only a benefit if the **callee authorizes the request** instead
of blindly executing it. Non-negotiables:

1. **The callee validates every request against an explicit allowlist — default deny.**
   Reuse the existing `providesTo` grant in `.fuze/manifest.json` (currently `[]` =
   accept no callers, fail-closed) as the authoritative "who may ask me." A request from
   a sender not on the allowlist is refused, logged, and never executed.
2. **Requests are capability-scoped, not arbitrary command execution.** The envelope's
   `cap=<capability>` names a *pre-agreed operation* (e.g. `kubectl.read`,
   `deploy.sync`), and the callee maps it to a vetted action — it does **not** `bash`
   whatever string the caller sends. Arbitrary-command delegation is banned.
3. **Credentials never cross the boundary.** The callee returns results/summaries only.
   No secret, token, or kubeconfig is ever placed in a reply.
4. **Irreversible/prod-affecting capabilities keep their existing human gate.** A
   delegated request does not bypass `always_ask`/`approve` or GitOps review — it is
   subject to the *same* controls as if a human asked in that environment.
5. **Verify the sender.** Trust the `from`/`reply_to` session id only insofar as the
   platform delivered it; treat envelope contents as untrusted input and validate.

This mirrors the frozen exec-tier A2A contract (`providesTo` is the authoritative,
callee-owned, fail-closed allowlist) rather than inventing a parallel trust model.
**Do not** flip `a2a.enabled` or widen `providesTo`/`servingRoles` here — those belong
to the exec-tier rollout PR with its sign-off, per `CLAUDE.md`.

---

## 5. Capability → environment registry

The caller needs to know *which environment* owns a capability. Seed set (extend as
capabilities are added), keyed to `roles.environments` in `.fuze/manifest.json`:

| Capability | Owning environment | Notes |
|---|---|---|
| prod cluster read (`kubectl get`, logs) | `selfhosted-devops` | already fronted read-only by `cluster-query.yml`; prefer that for pure reads |
| GitOps edit + PR (Helm/Argo/values) | `cloud-devops` | `FUZE_GITOPS_ONLY=true`, no kubeconfig — edits + PRs, never direct prod apply |
| backend / frontend / qa work | `cloud-backend` / `cloud-frontend` / `cloud-qa` | per-slice environments |
| exec decisions | `cloud-exec` (tenants `Exec-{ceo,cto,cfo,ciso}`) | governed by the frozen exec A2A card contract |
| **GitHub secret provisioning** | **not wired to any managed-agent env today** | `cloud-devops` has `gh` but `GITHUB_TOKEN` is unset (the proxy injects *git* creds only, not org/secret admin). Provisioning repo/org secrets needs an explicit token with secret-write scope added to the owning env (Phase 3) before a delegate can actually do it. |

A caller that needs a capability looks it up here and **spawns a Claude Code session in
that `environment_id`** (or `list_sessions`-finds a live one), then delegates — it never
asks for the credential itself, and it never needs a Managed-Agents `agent_id`.

> **Why the "orphaned managed agents" are a red herring for the working path.** The
> hand-created console agents are dark because (a) nothing invokes them, (b) the API path
> is credit-blocked, and (c) they are unregistered: `helm/fuzeinfra/files/handoff-state/
> agent-ids.json` is `{}`, so `spawn_agent`/`ask_agent` resolve no role→agent id and
> return "unknown role" — and a console agent like `agent_015…` appears **nowhere** in
> this repo. **But seeding that map is not the unblock** (§2a): it only converts "unknown
> role" into "insufficient credit", because that whole path is API-billed. The
> subscription-billed Claude Code path in §3 needs **none** of this — it spawns into the
> environment directly. Fixing/registering handoff-mcp (Phase 2) matters only if/when you
> specifically want the API-billed managed-agents surface; it is not required for
> capability delegation to work.

---

## 6. Phased plan (the "all 3")

- **Phase 1 — working transport + docs.** ✅ This document; the envelope + authz
  convention above; deprecation banner on the relay/socket bridge README.
- **Phase 1b — reference runbook + helper.** ✅ **`CAPABILITY_DELEGATION_RUNBOOK.md`**
  (the step-by-step every session follows at a capability wall — caller's 4 steps +
  callee's authorize-first) and **`capability_delegation.py`** (the deterministic pieces
  as importable functions + a CLI: `build_envelope`/`parse_envelope`, the
  capability→environment registry, `select_path` local-vs-remote, and the fail-closed
  `authorize`), guarded offline by `tests/test_capability_delegation.py`. `CLAUDE.md`
  carries the caller-guidance rule (delegate at a capability wall) that §0 named as
  missing. **Still owed here:** where skills are sourced from FuzeSDLC, a
  `capability-delegation` skill so agents reach for this at a boundary automatically —
  that lives in FuzeSDLC, not this repo.
- **Phase 2 — reconcile onto handoff-mcp (#1).** Make `spawn_agent`/`ask_agent`/
  `resume_session` actually work: unblock managed-agents provisioning (the Anthropic
  credit / role-state `{}` issue is external and must be resolved first), fix the CF
  Access bypass for machine callers, and — where useful — back `resume_session`/
  `ask_agent` with the Routines API so the semantic API has a working substrate. Fold in
  the authz model from §4 server-side.
- **Phase 3 — env/manifest authz wiring.** Populate the capability registry and the
  fail-closed `providesTo` grants for real delegation pairs, in a change that carries
  the security sign-off (not this PR). Retire the relay/gateway/socket code once #1/#3
  cover every consumer.

---

## 7. What NOT to do

- Do **not** hand-write frames to `CLAUDE_CODE_MESSAGING_SOCKET` / reverse-engineer
  `peerProtocol` — it is guarded internal IPC and unnecessary (transport #3 delivers a
  peer turn natively).
- Do **not** delegate arbitrary shell/command strings across the boundary — capabilities
  are named, pre-agreed operations only (§4.2).
- Do **not** return credentials in a reply (§4.3), and do **not** widen
  `a2a.enabled`/`providesTo`/`servingRoles` without the exec-tier sign-off (§4).
