# ARC Runner Onboarding Prompt

Copy the block below as the opening message in a new Claude Code session
**inside the target repo**.  Fill in the `<PLACEHOLDERS>` before sending.

---

```
I need you to install a GitHub Actions self-hosted runner for this repo using
the shared ARC (Actions Runner Controller) that already runs on the FuzeInfra
Contabo k3s cluster.

## Context

FuzeInfra hosts a shared ARC controller in the `arc-systems` namespace.
It watches the `arc-runners` namespace.  Each repo gets its own
AutoscalingRunnerSet (Helm release) in that namespace.  The controller then
spins up ephemeral runner pods on the `fuzeinfra-ci-runner-1` node
(label: fuzeinfra.io/pool=ci, taint: fuzeinfra.io/ci=true:NoSchedule).

The tooling for this lives in FuzeInfra:
  - Script:    runners/arc/register-repo.sh  (idempotent, helm upgrade --install)
  - Workflow template: runners/arc/workflow-template/arc-register.yml

## What I need you to do

1. Add a `.github/workflows/arc-register.yml` to THIS repo by copying the
   template from FuzeInfra (https://github.com/izzywdev/FuzeInfra, path
   runners/arc/workflow-template/arc-register.yml) and filling in:
     SCALE_SET_NAME: "<REPO_SLUG>"   # e.g. "fuzefront" — this becomes runs-on:
     USE_EXISTING_SECRET: "arc-runner-github-app"
   Leave GH_APP_* secrets empty (we share the existing k8s secret).

2. The workflow needs these repo secrets (add them if missing):
     KUBE_CONFIG  — base64-encoded kubeconfig for the FuzeInfra cluster.
                    Get it from: kubectl config view --raw | base64 -w0
                    (run on a machine that has access to the Contabo cluster)

   Since we're using USE_EXISTING_SECRET, no GH_APP_* secrets are needed
   provided the existing arc-runner-github-app k8s secret already covers
   this repo.  If it doesn't, you'll need to:
     a. Add the repo to the GitHub App's repository access in GitHub Settings →
        Developer settings → GitHub Apps → [app name] → Repository access
     b. Then re-run the workflow.

3. Commit the workflow file, push, and trigger it (workflow_dispatch, action=install).

4. Verify success:
   - `kubectl -n arc-runners get autoscalingrunnersets` shows a new entry for
     SCALE_SET_NAME
   - The GitHub repo's Settings → Actions → Runners shows the scale set listed

5. Update one existing workflow in this repo to test it — add a minimal job:
   ```yaml
   jobs:
     smoke:
       runs-on: <SCALE_SET_NAME>
       steps:
         - run: echo "runner works — $(hostname)"
   ```
   Confirm it runs to completion (not stuck in queued).

## Key constraints

- Scale set name must be unique across all repos sharing the arc-runners namespace.
  Existing names in use: "staging" (FuzeInfra).  Pick something repo-specific.
- All runners land on fuzeinfra-ci-runner-1 (4 CPU / 7.75 GB RAM, Contabo).
- The controller watchSingleNamespace is "arc-runners" — do NOT deploy to any
  other namespace or the controller will ignore it.
- Do NOT hand-deploy prod resources.  The arc-runners Argo CD app manages the
  FuzeInfra scale set via GitOps; the workflow-dispatched Helm install for new
  repos is the correct mechanism (Argo does not manage other repos' scale sets).

## Done criteria

- arc-register.yml workflow present in .github/workflows/ and merged to main
- Workflow ran successfully (action=install)
- kubectl confirms the AutoscalingRunnerSet exists
- Smoke test job ran on the self-hosted runner (not ubuntu-latest)
```

---

## Notes for the human

| Placeholder | What to fill in |
|---|---|
| `<REPO_SLUG>` | Short unique name for the runner, e.g. `fuzefront`, `fuzeops`. Becomes the `runs-on:` label. |
| `KUBE_CONFIG` secret | Run `kubectl config view --raw | base64 -w0` on a machine with Contabo cluster access and paste into the repo's secrets. |
| GitHub App access | If the smoke test job stays queued, check that the GitHub App (whose credentials are in `arc-runner-github-app`) has been granted access to this repo under GitHub Settings → Developer settings → GitHub Apps. |

The register-repo.sh script is idempotent — re-running the workflow is safe and
will upgrade the scale set in place.

## Docker / Docker-in-Docker (DinD)

Every scale set registered via `register-repo.sh` runs with **`containerMode: dind`**,
so runner pods get a **real Docker daemon** (a privileged `docker:dind` sidecar +
`init-dind-externals` initContainer, with `DOCKER_HOST` wired for the runner). This
means these work out of the box on `runs-on: <SCALE_SET_NAME>` — no separate
`kind-host` runner is needed for image builds:

- `docker build` / `docker buildx build --push` (GHCR image builds) ✅
- `docker run` / `docker ps` (daemon reachable via `DOCKER_HOST`) ✅

**`docker compose` needs one extra step.** The stock `ghcr.io/actions/actions-runner`
image ships the docker CLI + buildx but **not** the compose-v2 plugin, so
`docker compose -f …` fails with `unknown shorthand flag: 'f' in -f`. To enable it,
publish `runners/arc/Dockerfile` (bakes the compose plugin) and point the scale set
at it via `RUNNER_IMAGE` / the `image:` field, then re-register (below). DinD alone
does **not** add compose.

**Re-register existing scale sets to pick up DinD.** The dind sidecar only appears
on pods created *after* the Helm values change. Any scale set registered before DinD
was enabled must be re-registered once:

- Consumer repos: re-run your `arc-register.yml` (workflow_dispatch, action=install).
- FuzeInfra's own `staging` set: run `arc-reinstall-scaleset.yml` (or let the
  `arc-runners-staging` Argo CD app sync the updated `runner-scale-set-values.yaml`).

**Capacity note.** DinD raises per-runner limits to cpu:4 / mem:4Gi (requests stay
modest at cpu:500m / mem:1Gi so scheduling isn't blocked). The CI node
`fuzeinfra-ci-runner-1` is 4 CPU / 7.75 GB, so heavy parallel image builds across
many scale sets can contend — scale the CI pool (see the node-autoscaling design
doc) if builds start queuing on capacity.
