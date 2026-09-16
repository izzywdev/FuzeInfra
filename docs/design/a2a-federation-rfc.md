# RFC: A2A Federation — external LLM callers → FuzeFront front door → in-cluster product delegation

- **Status:** Draft (for exec-tier + security review)
- **Owning tiers:** platform-governance (standard), contract-designer (contract), a2a-maintainer (surface build), devops-engineer (GitOps apply), security/CISO (risk sign-off)
- **Related:** #954 (6 product A2A pods deploy-broken), #981 (A2A caller identity unbuilt at the IdP)
- **Frozen contract touched:** FuzeAgent `agent-templates/contracts/a2a/v1/**` (`card-projection.md`, `authz.md`) — changes here are contract-designer's, not this repo's.

> This RFC is a plan, not an implementation. Every load-bearing step is exec-tier + security-gated. Nothing in it authorizes flipping `a2a.enabled`, widening `providesTo`/`servingRoles`, hand-deploying, or `kubectl patch` on prod. It exists so the program is reviewable as one thing instead of a chat thread.

## 1. Goal

Let external LLM systems (Claude, GPT, Gemini, …) converse with the Fuze product family in free language over A2A, authenticated like a FuzeFront API caller, so an orchestrator can drive operations that span products — e.g. a request that plans in Jira (FuzePlan), launches a cloud agent (FuzeAgent), and reads BI (FuzeBI) in one conversation. The public entry point delegates internally, in-cluster, to per-product A2A pods; each hop is an authorized A2A call.

## 2. Current state (verified live, 2026-09-14/15)

| Layer | Reality today |
|---|---|
| **Product A2A pods** | `a2a-shared` (FuzeAgent multi-tenant server) and `a2a-fuzefront` are **1/1 Running** and correct. FuzeFront's Agent Card advertises its **own** `/rpc` (`inClusterUrl` correct), skill `app-shell-platform`, security `fuze-oidc` + `fuze-mtls`. **6 other product pods are deployed but failing** (config-error / init-crashloop) — #954. |
| **Callee auth** | Fail-closed and working: unauthenticated `POST /rpc` → `401`. `callerClaim: repo`, `audience: a2a`, issuer `app.fuzefront.com/application/o/fuzefront/`. |
| **Caller identity** | **Does not exist.** The shared server can't mint an `aud=a2a` / `repo=<caller>` token — no OIDC client mounted, no token-minting/exchange code. And **authentik has no A2A OIDC provider at all** (only user-login SSO clients: FuzeFront, mendys-platform, mendys-datasets, fuzeinfra-admin). #981. |
| **Router / front door** | **Does not exist as a product component.** FuzeInfra's `a2a-gateway`/`a2a-relay` pods are the deprecated **v0 Claude-*session* delivery bridge** (`claude -p --cloud` / WSS relay), unrelated to the product Agent-Card fabric. |
| **Public ingress** | Tunnel-only + Cloudflare Access **email-OTP** (a human gate). No machine-to-machine entry to A2A. |
| **Contract** | `contracts/a2a/v1` is frozen; `callerClaim` is `repo` (internal repo identities only). No external-caller class exists. |

**Net:** the *callee* half of A2A is built and correct; the *caller* half, the *router*, the *machine ingress*, and the *external-caller contract* do not exist yet.

## 3. Target architecture (four layers)

```
External LLM / orchestrator
      │  HTTPS + CF Access service-token  +  FuzeFront-issued A2A token (aud=a2a)
      ▼
[ Public A2A front door / router ]  ── in fuzefront (or a dedicated ns)
      │  in-cluster A2A message/send, one authorized hop per product,
      │  caller identity per serving repo (repo claim on callee providesTo)
      ├──▶ a2a-fuzeplan  ──▶ FuzePlan MCP gateway ──▶ Jira
      ├──▶ a2a-fuzeagent ──▶ launch cloud agent
      ├──▶ a2a-fuzebi    ──▶ BI reads
      └──▶ …             (each: product MCP gateway → product OpenAPI, classify/safety/upstream guards)
```

1. **Public front door (machine ingress).** A Cloudflare Tunnel ingress host (e.g. `a2a.fuzefront.com`) with a **CF Access service-token** policy for machines, in front of the pod's own bearer + mTLS. Terraform `for_each` in `terraform/contabo/cloudflare.tf` already models CF Access apps.
2. **Caller identity at the IdP (the missing foundation — #981).** An authentik OIDC provider issuing `aud=a2a` with a **`repo`-claim scope-mapping**, plus a confidential client per serving repo (or a shared client with per-caller `repo`). Reuses the existing Authentik + Permit spine.
3. **Router / orchestrator.** A real front-door agent that holds the registry of product Agent Cards, plans over their advertised skills, and delegates via in-cluster A2A. It runs on the **one shared image, config-only variation** — planning/routing lives in mounted role context + skills, **never** baked into the image, and it is **not** the deprecated `a2a-gateway`/`a2a-relay`.
4. **External-caller auth (contract change).** Admit a non-`repo` caller class: FuzeFront security-service mints scoped `aud=a2a` tokens for external tenants; **Permit PDP** decides which products/skills each caller may reach. This is a `contracts/a2a/v1` → v2 evolution owned by contract-designer.

## 4. Security (mandatory CISO/appsec review — this is the gating section)

A public, free-language door that can trigger cross-product operations (create Jira issues, **launch cloud agents = spend + compute**) is a large blast radius. Non-negotiables:

- **Per-caller skill scoping via Permit** — an external token authorizes an explicit, minimal skill set, not "all of FuzeFront."
- **Mutating/irreversible classification** — reuse the MCP gateway's `classify.ts`/`safety.ts`/`upstream.ts`; **no raw-REST fallback** (adds zero reachable ops, bypasses the guards).
- **Human-in-the-loop** for irreversible/costly ops reached through the door.
- **Prompt-injection / confused-deputy** defense at the front door: the orchestrator must treat external free text as data, and every downstream hop re-authorizes on the *original* caller identity (no privilege amplification across hops).
- **Rate limits + full audit logging** per caller, per skill.
- **Secrets boundary:** tokens/kubeconfig never cross a hop or land in a reply; the a2a-maintainer secret rules apply.

## 5. Phased rollout (dependency-ordered; each phase gated)

- **Phase 0 — fleet health (#954).** Root-cause and fix the 6 failing product pods (missing sealed secrets / init config), reconcile `values-contabo` A2A gates vs. each manifest so declared == deployed. *Owner: a2a-maintainer + devops.*
- **Phase 1 — caller identity (#981), internal only.** Create the authentik `aud=a2a` provider + `repo`-claim mapper + client(s); double-seal client secrets (IdP side in `fuzefront`, consumer side per pod ns); wire the runtime to fetch a client-credentials token. **Exit criterion: a real `200` on the FuzeAgent→FuzeFront `message/send`** (the round-trip that returns `401` today). *Owner: a2a-maintainer + devops; security review of the client/claim design.*
- **Phase 2 — internal delegation + router.** Stand up the front-door/router agent (config-only on the shared image); prove a two-hop in-cluster delegation (router → FuzePlan → Jira read) with per-hop authorized identities. Requires FuzePlan's pod actually shipped (it advertises A2A but ships none today). *Owner: a2a-maintainer + devops + product agents.*
- **Phase 3 — external-caller contract.** contract-designer evolves `contracts/a2a/v1` to admit an external-caller class; FuzeFront security-service mints external `aud=a2a` tokens; Permit scoping per external tenant. *Owner: contract-designer + security.*
- **Phase 4 — public ingress, last.** CF Access service-token host in front of the router; smallest attack surface lit only once 0–3 are solid and CISO signs off. *Owner: devops + security.*

## 6. Invariants (do not violate)

- One A2A image (`ghcr.io/izzywdev/fuzeagent-a2a`), config-only variation; **no second A2A Dockerfile**, no product logic in the image.
- `contracts/a2a/v1/**` is read-only truth; a needed field that doesn't exist is a `BLOCKED:` for contract-designer, not a cast or out-of-band key.
- Never flip `a2a.enabled` / widen `providesTo` / `servingRoles` to make a hop work — that is the exec-tier rollout PR with sign-off.
- Prod is GitOps: change `helm/fuzeinfra` (or values) + let Argo sync; never hand-deploy or `kubectl patch` a live prod resource.

## 7. Open questions

1. **One shared A2A client vs. per-repo clients?** Per-repo gives cleaner `repo`-claim provenance and revocation; shared is fewer secrets. Recommendation: per-repo, sealed per serving ns.
2. **Router placement** — reuse the FuzeAgent shared server as the router tenant, or a dedicated `a2a-router` tenant? Leaning dedicated, to keep the exec/product tenants clean.
3. **External tenant model** — does an external caller map to a synthetic `repo`-style claim, or a distinct `client`/`tenant` claim the contract adds in v2? (contract-designer decision.)
4. **mTLS for external callers** — the callee wants `fuze-mtls` as defense-in-depth; how does an external machine present a client cert through the tunnel, or is mTLS in-cluster-only with the token as the external gate?

## 8. What this RFC does not do

No prod change, no contract edit, no secret creation, no pod deploy. It is the reviewable plan that the exec-tier rollout PR(s) will implement, phase by phase, each with its own PR, tests, and sign-off.
