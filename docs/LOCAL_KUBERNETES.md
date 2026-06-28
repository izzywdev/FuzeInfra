# Run FuzeInfra locally on Kubernetes — getting started on your machine

FuzeInfra ships the same stack two ways: the legacy **docker-compose** path
(`./infra-up.sh`) and the **Kubernetes** path (a Helm chart on **kind** locally,
mirroring EKS/Contabo prod). This guide is the Kubernetes path — the one that
matches production. If you just want services on localhost ports fast, the
compose path in the main README is fine; use this when you want prod parity.

> One command once set up: **`make kind-up`** (full stack) → **`make kind-validate`**
> (prove it's healthy) → **`make kind-test`** (functional smoke) → **`make kind-down`**.

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
make kind-up
#   └─ creates the kind cluster, installs ingress-nginx + cert-manager + the
#      local-CA issuer, and `helm upgrade --install`s the chart (values-local.yaml)
```

```powershell
# Windows PowerShell (no make needed)
.\k8s\kind\setup-kind.ps1
```

When it finishes, check it:

```bash
make kind-status            # kubectl -n fuzeinfra get pods
```

Reach the UIs by adding the hostnames it prints to your hosts file (all →
`127.0.0.1`), e.g. `grafana.dev.local`, `prometheus.dev.local`. Local HTTPS via
the `fuzeinfra-local-ca` issuer is covered in [LOCAL_TLS.md](LOCAL_TLS.md).

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

It runs on a **host-level self-hosted runner** (`runs-on: [self-hosted, kind-host]`)
because kind needs Docker + real RAM, which the hardened in-pod ARC `staging`
runners can't provide. Register that runner once — see
[runners/README.md → Host-level kind runner](../runners/README.md#host-level-kind-runner-for-the-kind-validate-gate).
Prefer zero infra? The same commands run as a `pre-push` git hook (also documented
there).

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
