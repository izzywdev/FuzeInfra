# Contabo externalgrpc — kind integration harness

Proves the Cluster Autoscaler (CA) <-> Contabo externalgrpc provider gRPC
loop end-to-end on a local Kubernetes cluster, **without any real Contabo
account**. The provider runs in `FAKE_CLOUD=1` mode, backed by
[`internal/contabo.MemClient`](../../internal/contabo/memclient.go) — an
in-memory, mutex-guarded instance list that stands in for the real Contabo
REST API. See `cmd/server/main.go`'s `FAKE_CLOUD` env var documentation for
how the fake client is selected and which credential requirements it
relaxes.

## >>> PROMINENT: do NOT run this on the primary FuzeInfra dev host <<<

**This script cannot run on the current dev machine.** Its Docker Desktop
engine uses **cgroup v1**, and kind's control-plane container will not
bootstrap under cgroup v1 — both 3-node and single-node kind configs hang
indefinitely at `Starting control-plane`. This is the same blocker recorded
in project memory (`project_kind_cgroupv1_blocker`) for the main FuzeInfra
kind stack (`k8s/kind/`).

Run `kind-autoscaling-test.sh` in one of these environments instead:

- **CI**, on a host-level self-hosted runner that actually has kind/docker
  with a cgroup-v2 (or Docker-Desktop-with-WSL2) engine. This repo already
  has the pattern for that: see `.github/workflows/kind-validate.yml`
  (label `kind-host`, `runs-on: [self-hosted, kind-host]`) and
  `k8s/kind/setup-kind.sh` / `teardown-kind.sh` for the FuzeInfra-wide kind
  bring-up convention. Wire a new job (or extend that workflow) to also
  invoke this script — it was **not** added to `kind-validate.yml` as part
  of this task, since that workflow currently only stands up the base
  FuzeInfra stack, not the Cluster Autoscaler chart path.
- **A WSL2-backed Docker Desktop** ("Use the WSL 2 based engine" enabled)
  or any native Linux Docker host — both give a cgroup v2 cgroup driver
  that kind's control-plane image expects.

Do not attempt to "fix" a hang by waiting longer or retrying — the
bootstrap does not complete on cgroup v1 at all.

## What this proves

1. `kind create cluster` — a real (if small) Kubernetes control plane.
2. The provider image builds from this module's `Dockerfile` and loads into
   kind with no external registry.
3. `helm upgrade --install helm/fuzeinfra` with
   `clusterAutoscaler.enabled=true` stands up:
   - the upstream `cluster-autoscaler` Deployment configured with
     `--cloud-provider=externalgrpc` pointed at the provider's in-cluster
     Service (see `helm/fuzeinfra/templates/autoscaler/cluster-autoscaler.yaml`
     and `provider-deployment.yaml`);
   - the `contabo-externalgrpc` provider Deployment, with `FAKE_CLOUD=1`
     injected via a Secret the script creates
     (`fuzeinfra-ca-provider`, matching `clusterAutoscaler.provider.existingSecret`).
4. An intentionally unschedulable `Deployment` (huge CPU/memory request)
   drives CA to call `NodeGroupIncreaseSize` on the provider, which in fake
   mode calls `MemClient.Create` — logged as `[fake-cloud] Create id=... `.
   The script polls the provider pod's logs for that line.
5. Scaling the demo `Deployment` back to 0 frees the elastic node; once it
   sits idle past `scale-down-unneeded-time` (set to `1m` by this script,
   well below the chart's `45m` default, so the test finishes in minutes
   not the better part of an hour), CA calls `NodeGroupDeleteNodes`, which
   in fake mode calls `MemClient.Delete` — logged as
   `[fake-cloud] Delete id=...`. The script polls for that line too.

Any missing log line before its timeout is a non-zero exit (`fail()` calls
`exit 1`), so this is safe to wire into a CI gate once it has a runner that
can execute it.

## Prerequisites (only relevant where you DO run it — see warning above)

- Docker (cgroup v2 / WSL2-backed, or native Linux)
- [`kind`](https://kind.sigs.k8s.io/)
- `kubectl`
- `helm` (v3)
- This repo checked out with the `helm/fuzeinfra` chart present (the script
  resolves it relative to its own path: two directories up from
  `cluster-autoscaler/contabo-externalgrpc/` to the FuzeInfra repo root,
  then `helm/fuzeinfra`).

## Usage

```bash
cd cluster-autoscaler/contabo-externalgrpc
bash test/integration/kind-autoscaling-test.sh
```

Environment overrides (all optional, see top of the script for defaults):

| Variable | Purpose |
| --- | --- |
| `KIND_CLUSTER_NAME` | kind cluster name (default `ca-fake-test`) |
| `NAMESPACE` | k8s namespace the chart is installed into (default `fuzeinfra`) |
| `RELEASE_NAME` | Helm release name (default `fuzeinfra`) |
| `PROVIDER_IMAGE` | local image tag built + `kind load`-ed (default `fuzeinfra-contabo-ca-provider:fake-test`) |
| `HELM_CHART` | path to the chart (default `helm/fuzeinfra` at repo root) |
| `INCREASE_TIMEOUT` | seconds to wait for the `IncreaseSize`/`Create` log line (default `180`) |
| `DELETE_TIMEOUT` | seconds to wait for the `DeleteNodes`/`Delete` log line (default `300`) |
| `KEEP_CLUSTER` | set to `1` to skip the `kind delete cluster` on exit, for post-mortem inspection (default `0`, always tears down) |

The script deletes the kind cluster in an `EXIT` trap by default (pass or
fail) so repeated runs don't accumulate clusters; set `KEEP_CLUSTER=1` to
leave it running for debugging, then `kind delete cluster --name
ca-fake-test` (or your `KIND_CLUSTER_NAME`) manually when done.

## What was verified without kind (this task)

Since the dev host cannot run kind, verification here was limited to the
Go level:

- `go build ./...` — the module compiles, including the new
  `internal/contabo/memclient.go` and the `FAKE_CLOUD` branch in
  `cmd/server/main.go`.
- `go test ./... -race -count=1` — all unit tests pass, including
  `internal/contabo/memclient_test.go` (Create -> ListByTag -> Delete
  round-trip, tag filtering, not-found-on-delete, incrementing IDs) and the
  `FAKE_CLOUD` cases in `cmd/server/main_test.go` (fake mode succeeds
  without Contabo/K3S credentials while still requiring
  `PRODUCT_ID`/`IMAGE_ID`/`REGION`/`SSH_KEY_ID` and valid `MIN_SIZE`/
  `MAX_SIZE`; real mode is unchanged and still errors without credentials).
- `bash -n test/integration/kind-autoscaling-test.sh` — shell syntax check
  only. **The script itself has not been executed** — see the warning
  above. Its actual kind run is deferred to CI or a WSL2/cgroup-v2 engine.
