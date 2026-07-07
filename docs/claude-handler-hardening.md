# Hardening the `@claude` cluster handler (issue #78)

Follow-up to the security review of #77. The `@claude` handler (`.github/workflows/claude.yml`)
can perform production cluster operations autonomously. #77 added an author gate,
destructive-op guard shims, and SHA-pinned actions. This document covers the **durable**
control: moving the handler from cluster-admin credentials bounded by OS shims to
**least-privilege credentials bounded by RBAC and a fine-scoped GitHub App token**.

## 1. Scoped ServiceAccount kubeconfig (replaces cluster-admin `KUBE_CONFIG`)

The cluster-admin k3s kubeconfig is replaced by a dedicated ServiceAccount,
`claude-handler` (namespace `fuzeinfra-automation`), whose RBAC grants only what the
handler needs:

| Resource | Verbs | Scope | Notes |
|---|---|---|---|
| `secrets` | get/list/watch/create/update/patch | `fuzeinfra`, `fuzeinfra-runners` | **No delete.** Not bound in `kube-system`/`argocd`/`cert-manager`. |
| `applications`, `applicationsets`, `appprojects` (argoproj.io) | get/list/watch | `argocd` | **Read-only** — prod is GitOps with `selfHeal`; mutation must go through git. |
| `nodes` | get/list/watch/patch | cluster-wide | Enables `kubectl label nodes`. Patch on Nodes grants nothing on workloads. |

Explicitly **not** granted: cluster-admin, delete on any resource, write on Deployments/
StatefulSets/Pods, access to `kube-system`/`argocd` Secrets, or ArgoCD app mutation.
Even if an injected agent resolves the real `kubectl` binary (bypassing the guard shims),
the API server rejects anything outside these rules.

### Apply

```bash
# 1. Apply RBAC (run once, as a human/CD with admin context):
kubectl apply -f k8s/claude-handler/rbac.yaml

# 2. Mint a kubeconfig for the scoped SA and store it as the KUBE_CONFIG secret:
bash scripts/generate-claude-handler-kubeconfig.sh | gh secret set KUBE_CONFIG --repo izzywdev/FuzeInfra
```

`generate-claude-handler-kubeconfig.sh` defaults to a **short-lived** bound token
(`kubectl create token`, TTL `2h`). For runners that cannot refresh tokens, use
`MODE=longlived` to create a non-expiring SA-token Secret (still RBAC-scoped). See the
script header for all options (`TOKEN_TTL`, `APISERVER`, `MODE`).

Keep the cluster-admin kubeconfig for humans and CD (ArgoCD) **only**.

### Extending scope

If the handler needs Secrets access in another namespace, copy the
`claude-handler-secrets` Role + RoleBinding into that namespace. Prefer adding a
narrowly-scoped namespaced Role over widening to a ClusterRole.

### Verify the scope

```bash
KCFG=$(mktemp); bash scripts/generate-claude-handler-kubeconfig.sh > "$KCFG"
kubectl --kubeconfig "$KCFG" auth can-i create secrets -n fuzeinfra        # yes
kubectl --kubeconfig "$KCFG" auth can-i delete secrets -n fuzeinfra        # no
kubectl --kubeconfig "$KCFG" auth can-i delete deployments -n fuzeinfra    # no
kubectl --kubeconfig "$KCFG" auth can-i patch applications -n argocd       # no
kubectl --kubeconfig "$KCFG" auth can-i patch nodes                        # yes
kubectl --kubeconfig "$KCFG" auth can-i get secrets -n kube-system         # no
rm -f "$KCFG"
```

## 2. Fine-scoped GitHub App token (replaces the broad PAT)

> **Note:** the `claude.yml` workflow file must be edited by a human — the GitHub App
> backing this handler cannot modify files under `.github/workflows/`. The snippet below
> is the change to apply.

Replace the broad `GH_TOKEN` PAT with a short-lived **GitHub App installation token**,
minted per run and scoped to the specific repos/permissions:

- `contents: write` + `pull_requests: write` — only for the fix branch / PR.
- `packages: read` — GHCR pulls.
- Scoped to **this repository only** (not the whole org/account).

Create a GitHub App (or reuse the handler's App), grant it exactly those permissions,
install it on `izzywdev/FuzeInfra`, and store `APP_ID` + `APP_PRIVATE_KEY` as secrets.
Then mint the token in-workflow:

```yaml
    steps:
      - name: Mint scoped GitHub App token
        id: app-token
        uses: actions/create-github-app-token@<pinned-sha>  # v1.x
        with:
          app-id: ${{ secrets.CLAUDE_APP_ID }}
          private-key: ${{ secrets.CLAUDE_APP_PRIVATE_KEY }}
          owner: izzywdev
          repositories: FuzeInfra
          permission-contents: write
          permission-pull-requests: write
          permission-packages: read

      - name: Checkout
        uses: actions/checkout@<pinned-sha>  # v4
        with:
          token: ${{ steps.app-token.outputs.token }}
          fetch-depth: 1

      - name: Run Claude
        uses: anthropics/claude-code-action@<pinned-sha>  # v1
        env:
          # Scoped SA kubeconfig from section 1 (NOT cluster-admin):
          KUBE_CONFIG: ${{ secrets.KUBE_CONFIG }}
          # Short-lived, repo-scoped App token (NOT a broad PAT):
          GH_TOKEN: ${{ steps.app-token.outputs.token }}
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

The installation token is automatically revoked at job end, so the credential is
short-lived per run.

## 3. Ephemeral namespace-scoped context (optional)

Where the handler only needs to act within a single namespace, run it against a
short-lived namespace and bind the Secrets Role there, leaving the cluster-wide
`nodes` ClusterRole as the only non-namespaced grant. The `fuzeinfra-automation`
namespace already isolates the SA identity from workload namespaces.

## Defense in depth

These RBAC controls **complement** — they do not replace — the existing controls from
#77 (author gate, guard shims, SHA-pinned actions). The shims still provide a fast,
in-band block; RBAC is the backstop the API server enforces unconditionally.

---

## 4. Editing the @claude handler — runbook

> **Context:** On 2026-07-07, a bad SHA (`428971d...`) was pinned as "v1" of the
> claude-code-action. That commit was a dev/test commit in the action repo; its
> action code ran the action's own e2e playwright suite instead of the delegated
> task. Claude finished in 0s with no commits; the subsequent branch-compare step
> 404'd. This section documents how to prevent recurrence.

### The one rule: always verify the SHA before pinning

The `uses:` line in a GitHub Actions step pins to a **commit SHA in the action's own
repository**, not the consuming repo. If the SHA points to a development commit, the
action's dev/test code runs inside your job.

**How to get the correct SHA for a release tag:**

```bash
# Look up the SHA the upstream tag points to:
git ls-remote https://github.com/anthropics/claude-code-action.git refs/tags/v1.0.166
# → f87768c6d25f92ae6efa7175e223ef77d4cbf97f  refs/tags/v1.0.166

# Then use:
uses: anthropics/claude-code-action@f87768c6d25f92ae6efa7175e223ef77d4cbf97f  # v1.0.166
```

Or check the upstream releases page: https://github.com/anthropics/claude-code-action/releases

### What NOT to put in the prompt / system-prompt

- **No repo-specific test commands** (npm test, pytest, playwright, etc.). These
  belong in separate workflow steps, not inside the claude step's prompt/args.
- **No absolute paths** that assume a specific checkout layout.
- **No secrets in plaintext**. All secrets must be passed via `${{ secrets.NAME }}`.

### Valid inputs for claude-code-action@v1

| Input | Purpose | Notes |
|---|---|---|
| `anthropic_api_key` | Anthropic API key | Required |
| `prompt` | Task instructions | Omit for event-driven mode (reads issue/comment) |
| `claude_args` | Extra CLI flags | e.g. `--permission-mode bypassPermissions --max-turns 60` |
| `additional_permissions` | Extra tool grants (newline list) | e.g. `Bash`, `Write`, `Edit` |
| `github_token` | Override GitHub token | Defaults to `GITHUB_TOKEN` |
| `use_sticky_comment` | Collapse updates into one comment | Boolean |

Inputs **NOT** valid in v1: `allowed_tools`, `permission_mode`, `direct_prompt`,
`override_prompt` (all removed in v1; see migration guide).

### Testing a handler change

1. Create a draft PR for the change.
2. Open a test issue in this repo (or a sandbox repo) with the body `@claude hello`.
3. Verify the handler posts a real response (not "Claude finished ... in 0s").
4. Check the Actions run logs: the Claude step should show actual tool calls, not
   a 0-second exit.
5. Verify no branches are left dangling (clean up `claude/test-*` branches).

### Required status checks

The `actionlint` workflow (`.github/workflows/actionlint.yml`) must pass on every PR
touching `.github/workflows/**`. Make it a REQUIRED status check in branch protection.
CODEOWNERS requires a review from `@izzywdev` for all `claude*.yml` and related handler
files — this is enforced by the repo's branch protection ruleset.
