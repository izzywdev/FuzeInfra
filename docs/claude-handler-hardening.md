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
