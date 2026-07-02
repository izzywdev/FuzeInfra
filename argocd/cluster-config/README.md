# `argocd/cluster-config/`

FuzeInfra's own cluster-scoped bootstrap manifests — the things ArgoCD cannot
self-seed because they are what makes ArgoCD able to work. Applied by
`.github/workflows/apply-cluster-config.yml` on every commit here (via the
`KUBE_CONFIG` secret; SSH-free, no manual kubectl).

## What lives here

- **`fuzeinfra-argocd-github-app.sealed.yaml`** — a SealedSecret that decrypts to
  an ArgoCD **repo-credential template** (`argocd.argoproj.io/secret-type:
  repo-creds`) scoped to `https://github.com/izzywdev`. It carries a **GitHub App**
  (Contents:read) so ArgoCD can read **every** `izzywdev/*` private repo (20+)
  with **no per-repo credential**. New private repos are covered automatically —
  this is the zero-touch answer to "ArgoCD can't read my private repo."

## Rotating the GitHub App key

Re-generate the App private key, re-seal against the cluster's public cert, and
commit the new `*.sealed.yaml` — the workflow re-applies it. Plaintext never
touches git. See `docs/SECRETS_MANAGEMENT.md`.
