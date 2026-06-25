# Standard GitHub automation stack — installer templates

Canonical, replicable workflow templates for the FuzeOne standard automation
stack. Every repo we create (and existing ones — FuzeInfra, FuzeFront) should
stay in parity with these. FuzeInfra's **live** `.github/workflows/` remains the
reference implementation; these are the parameterized, install-ready copies.

## What's here

| Template | Install as | Purpose |
|---|---|---|
| `claude.yml` | `.github/workflows/claude.yml` | The `@claude` issue/PR handler. **Expert-first** + cross-repo hand-off + trusted-author gate. |

(Other stack members — `claude-ci-autofix.yml`, `helm-validate.yml`, `auto-merge.yml`, `telegram-pr-merged.yml`, the deliverable-verification check — live in FuzeInfra's `.github/workflows/`; copy them across the same way. This dir grows as they're parameterized.)

## Installing the `@claude` handler into a repo

1. Copy `claude.yml` → the target repo's `.github/workflows/claude.yml`.
2. Replace every `__REPONAME__` with the repo's short name, lowercased
   (repo `FuzeFront` → `fuzefront`). This is the `<reponame>-expert` agent the
   handler starts with.
3. Add the `ANTHROPIC_API_KEY` secret (and `GH_TOKEN` if cross-repo / package ops
   are needed).
4. **Cluster access** is OFF by default. Only repos whose `@claude` must perform
   live cluster operations keep the block marked *OPTIONAL: cluster access*
   (and add `KUBE_CONFIG` + the guard-shim step — see FuzeInfra's live
   `claude.yml` for the full, SHA-pinned version with kubectl/helm/terraform +
   the destructive-op guard shims).

## Expert-first behavior (the key contract)

On every task the handler **starts with the repo's expert agent**
(`<reponame>-expert`) to load architecture/deploy/gotcha context — matching the
global SDLC guideline "Start with the repo's expert agent."

If that agent **doesn't exist yet**, the handler **bootstraps it**:
1. Reads `.claude/templates/repo-expert.template.md` (the repo-expert template).
2. Explores the repo to fill it in (what it is, layout, components, build/deploy,
   gotchas) and writes `.claude/agents/<reponame>-expert.md`.
3. Opens a PR adding it, then uses it for the task.

So a repo with the standard handler installed becomes self-bootstrapping: the
first `@claude` task in a repo without an expert creates one for every task
after it. Ship `.claude/templates/repo-expert.template.md` alongside the handler
(it's repo-agnostic — copy it as-is).
