# Run FuzeInfra locally on Kubernetes — getting started on your machine

FuzeInfra ships the same stack two ways: the legacy **docker-compose** path
(`./infra-up.sh`) and the **Kubernetes** path (a Helm chart on **kind** locally,
mirroring EKS/Contabo prod). This guide is the Kubernetes path — the one that
matches production. If you just want services on localhost ports fast, the
compose path in the main README is fine; use this when you want prod parity.

> **One command: `make dev`.** It stands the stack up *and* verifies it —
> cluster + addons + chart, then "every enabled service is Ready and reachable",
> then the functional smoke suite. Tear down with `make dev-down`.
>
> The per-merge CI gate runs the *identical* command
> (`python3 scripts-tools/devenv.py up --fresh --profile full`), so if `make dev`
> would break on your machine, the gate goes red on the PR that broke it rather
> than on you.
>
> The lower-level `make kind-up` / `kind-validate` / `kind-test` / `kind-down`
> targets still exist and still work — `make dev` is the three of them, in order,
> with preflight checks and failure diagnostics.

---

## 1. Prerequisites — what you need installed

| Tool | Why | Install |
|------|-----|---------|
| **docker** | kind runs the cluster as containers | Docker Desktop (Win/Mac) · `docker` engine (Linux) |
| **kind** | the local Kubernetes cluster | https://kind.sigs.k8s.io · `choco install kind` · `brew install kind` |
| **kubectl** | talk to the cluster | `choco install kubernetes-cli` · `brew install kubectl` |
| **helm** | deploy the chart | `choco install kubernetes-helm` · `brew install helm` |
| **python 3** | the validate / smoke scripts | python.org · already present on most machines |

That's it — **docker + kind + kubectl + helm** (plus python for the validators).
`make` is optional: on Windows you can call the PowerShell scripts directly.
A GitHub Actions runner binary is only needed if you host the per-merge gate
(see §6) — not for local use.

Give Docker enough headroom: the **full** stack wants ~6–8 GB RAM. On a smaller
machine, deploy a [profile](#3-turn-services-on--off-profiles) instead.

---

## 2. One-command bring-up

```bash
# macOS / Linux / WSL / Git-Bash
make dev
```

```powershell
# Windows PowerShell (no make needed)
python scripts-tools\devenv.py up
```

That single command runs four phases and stops at the first one that fails:

| Phase | What it does | Failure looks like |
|---|---|---|
| **preflight** | tools on PATH, docker daemon up, host ports 80/443 free | a named missing tool with its install URL, or the process holding the port |
| **deploy** | kind cluster + ingress-nginx + cert-manager + local-CA issuer + `helm upgrade --install` | the underlying command's own error |
| **verify** | every *enabled* service has a Ready workload and answers a probe | a service × {ready, reachable} matrix |
| **smoke** | the pytest suite against the live cluster via port-forward | the failing test |

On any failure it dumps the non-Ready pods with their events and logs, and
**leaves the environment running** so you can poke at it. A bare "timed out" is
not an actionable error, so it does not produce one.

Useful variants:

```bash
make dev PROFILE=minimal   # postgres + redis only — fast, small machines
make dev-fresh             # delete the cluster first: proves a from-scratch build
make dev-dd                # use Docker Desktop's Kubernetes instead of kind
make dev-verify            # re-check what's already running (no redeploy)
make dev-status            # what's deployed right now
make dev-down              # tear down
```

**Docker Desktop's built-in Kubernetes** is a first-class backend, not a
second implementation: `--backend docker-desktop` runs the same
`setup-kind.sh` with `--no-cluster`, so the addons and the chart deploy are
byte-identical to the kind path. The one difference is forced by the platform —
ingress-nginx's `cloud` manifest instead of its `kind` one, because the `kind`
manifest pins the controller to a node label Docker Desktop's node does not have
and would otherwise sit Pending forever.

When it finishes, check it:

```bash
make kind-status            # kubectl -n fuzeinfra get pods
```

Reach the UIs by adding the hostnames it prints to your hosts file (all →
`127.0.0.1`), e.g. `grafana.dev.local`, `prometheus.dev.local`. Local HTTPS via
the `fuzeinfra-local-ca` issuer is covered in [LOCAL_TLS.md](LOCAL_TLS.md).

### Alternative: Docker Desktop's built-in Kubernetes (no kind)

Docker Desktop ships a single-node Kubernetes — enable it in **Settings →
Kubernetes → Enable Kubernetes**. It's the simplest path on Windows/macOS and
works where kind can't (notably a Docker Desktop still on **cgroup v1**, which
kind's control-plane can't bootstrap — check with `docker info | grep -i cgroup`;
if it says v1, either use this path or switch Docker to the WSL 2 engine).

```bash
make dd-up            # deploy the chart to the docker-desktop context
make kind-validate    # validator/test target the *current* context
make kind-test
make dd-down          # uninstall
```

Same `values-local.yaml` (Docker Desktop provides the `standard` storageClass).
ingress-nginx/cert-manager aren't preinstalled, so `*.dev.local` routing won't
work out of the box — reach services via `kubectl port-forward` (`make kind-test`
does this) or install ingress-nginx separately. Verified: all 19 services come up
Ready.

---

## 3. Turn services on / off (profiles)

Every service has an `enabled` gate in `helm/fuzeinfra/values.yaml`. Different
consuming repos need different subsets — one needs Mongo, another Neo4j, another
Kafka — so you can deploy exactly what you need.

```bash
make kind-profile PROFILE=minimal       # ./k8s/kind/setup-kind.sh --profile minimal
#                                          .\k8s\kind\setup-kind.ps1 -Profile minimal
```

| Profile | Services | Use for |
|---------|----------|---------|
| `minimal` | Postgres + Redis | a service that only needs a DB + cache |
| `data-stores` | all databases (Postgres/Mongo/Redis/Neo4j/ES/Chroma) | data-heavy apps, no messaging/monitoring |
| `full` | everything (default = `make kind-up`) | prod parity / the CI gate |

Ad-hoc trimming works too: `make kind-up` then re-run with
`helm upgrade ... --set kafka.enabled=false`, or add a file to
`helm/fuzeinfra/profiles/`.

> **Consumers:** annotate your namespace so its CRIT logs route to *your* repo:
> `kubectl annotate ns <ns> fuzeinfra.io/owner-repo=<owner>/<repo> --overwrite`
> (see [crit-log-autofix.md](crit-log-autofix.md)).

---

## 4. Validate — prove the whole env is deployable

```bash
make kind-validate
```

Reads which services are enabled, waits for every workload to become **Ready**,
asserts none are missing, runs in-cluster reachability probes, and prints a
`service × {ready, reachable}` matrix. Exits non-zero if any enabled service
isn't deployable — this is the "the entire env stands up" guarantee.

## 5. Test — functional smoke (the existing pytest suite)

```bash
make kind-test
```

Port-forwards each service to the localhost ports the `tests/` suite expects and
runs `pytest tests/` against the live cluster — real connectivity, no test
rewrite. (`scripts-tools/kind_port_forward.py` does the forwarding.)

Tear it all down when done:

```bash
make kind-down
```

---

## 6. Per-merge gate — keep the local deployment working

`.github/workflows/kind-validate.yml` runs the **full** bring-up + validate +
smoke on **every PR** that touches the chart, kind scripts, profiles, tests, or
the validators — so the local deployment can never silently rot.

It runs on **GitHub-hosted `ubuntu-latest`** — no runner to register, nothing to
keep switched on. And it runs the *same command you do*:

```bash
python3 scripts-tools/devenv.py up --fresh --profile full   # == make dev-fresh
```

That identity is deliberate. The gate is not a separate CI-shaped approximation
of the local experience; it *is* the local experience, executed on a clean
machine every time.

> It used to require a self-hosted `kind-host` runner on a developer's machine.
> That runner went offline on 2026-07-24 and the gate silently stopped running
> for days while still showing as "pending" on PRs. A gate whose liveness depends
> on somebody's laptop is a gate that will keep going quiet — see
> [runners/README.md](../runners/README.md#host-level-kind-runner--retired-no-longer-required).

### Local pre-push gate (recommended on this machine)

No host runner needed. A versioned hook at **`.githooks/pre-push`** runs a fast,
**real** deployability check before every push — `helm lint` plus a **server-side
dry-run** of the rendered chart against whatever local cluster is reachable
(docker-desktop or kind). The server-side dry-run validates against the live API
server + its CRDs, so it catches apply-time failures (like a Traefik-only CRD on
a non-Traefik cluster) that static `kubeconform` misses — in seconds, with no full
deploy. It only fires when chart/k8s files changed.

Activate it once per clone:

```bash
git config core.hooksPath .githooks
```

- Skip for one push: `git push --no-verify`
- Want the full gate locally? `make dev-fresh` (or `make dev-dd` to run it on
  Docker Desktop's Kubernetes) — that is exactly what CI runs.

---

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Pods `Pending` / OOM | Docker has too little RAM — raise it, or use `PROFILE=minimal`/`data-stores` |
| `helm ... timed out` | Heavy images (Elasticsearch, Kafka) are still pulling — `make kind-status`, re-run is idempotent |
| PVC won't bind | Local uses `storageClass: standard` (kind's provisioner) — don't set the prod `local-path` |
| `*.dev.local` won't resolve | Add the hostnames to your hosts file (→ `127.0.0.1`) or use the dnsmasq service |
| Cert not trusted | Import the local CA — see [LOCAL_TLS.md](LOCAL_TLS.md) |
| `kubectl` hits the wrong cluster | `kubectl config use-context kind-fuzeinfra` |

---

See also: [kubernetes-migration.md](kubernetes-migration.md) (kind/EKS/Contabo
overview), [gitops.md](gitops.md) (Argo CD / prod), [LOCAL_TLS.md](LOCAL_TLS.md).
