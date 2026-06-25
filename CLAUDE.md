# CLAUDE.md — FuzeInfra overlay (L1)

This repo **extends the FuzeSDLC baseline** (L0):
<https://github.com/izzywdev/FuzeSDLC/blob/main/CLAUDE.baseline.md>.

**Precedence:** this `CLAUDE.md` overrides the baseline; the baseline overrides defaults. All generic org policy — governance model, the agent roster + single-responsibility/done-contract, contract-first fan-out, design-system-first frontend, verification protocol, signed-commit/trailer conventions, async-orchestration, cross-repo `@claude` delegation, and RTK — lives in the baseline and is **not** restated here. Read the baseline first; this file carries only what is specific to FuzeInfra.

```yaml
tier: infra
expert: fuzeinfra-expert
```

Consult **`fuzeinfra-expert`** first for architecture/deploy/gotcha context (it advises; it does not gate). Per-repo agents/skills/hardening are declared in `.fuze/manifest.json` and sourced from FuzeSDLC.

---

## What FuzeInfra is (boundary)

The **shared, containerized infrastructure platform** — generic infra **only**, no application code. Apps attach via the external Docker network **`FuzeInfra`** (compose) or by addressing services in the **`fuzeinfra`** k8s namespace. Keeping app-specific things out of this repo is the whole point. App repos never edit FuzeInfra directly — they delegate via `@claude` (baseline §8).

## Dual delivery model

FuzeInfra ships the **same stack two ways**; a change usually has to land in both:

- **`docker-compose.FuzeInfra.yml`** — the legacy/local single-file orchestration (`docker-compose.ci.yml` = slimmed CI stack). Quickest local path: `python scripts-tools/setup_environment.py` → `./infra-up.sh` (`infra-up.bat` on Windows) / `./infra-down.sh`. The **`FuzeInfra` network is external** — it must exist (`infra-up` / `docker network create FuzeInfra`) before dependent app stacks come up.
- **`helm/fuzeinfra/`** — the **current** model: one umbrella chart run on **kind (local)**, **EKS (AWS)**, **Contabo k3s (prod)**. Values overlays `values.yaml` → `values-local.yaml` / `values-aws.yaml` / `values-contabo.yaml`. **Every service is behind an `enabled` gate** in `values.yaml` and must be wired into all relevant overlays. Local mirror-of-prod: `bash k8s/kind/setup-kind.sh` then `helm upgrade --install fuzeinfra helm/fuzeinfra -n fuzeinfra --create-namespace -f helm/fuzeinfra/values-local.yaml`.

## GitOps + self-heal (prod is GitOps — non-negotiable)

Prod is **Contabo single-node k3s**, namespace `fuzeinfra`, owned by **Argo CD** (`argocd/applications/fuzeinfra-prod.yaml`, `targetRevision: main`, `values-contabo.yaml`, `automated: {prune:true, selfHeal:true}`, ServerSideApply).

- **Never hand-deploy and never `kubectl patch`/`kubectl edit` a live prod resource** — Argo `selfHeal` reverts out-of-band changes within seconds. To change prod: edit `helm/fuzeinfra` (or its values), commit to `main`, let Argo sync. (This bit the Grafana dashboard fix — the patch didn't persist; it had to go through Git.)
- **Ingress is tunnel-only.** Traefik is pinned to `ClusterIP` (`argocd/cluster-bootstrap/traefik-clusterip.yaml`) so k3s servicelb doesn't bind hostPorts 80/443; all ingress flows through the **Cloudflare Tunnel** (`*.prod.fuzefront.com` → Traefik → Ingress), with **Cloudflare Access** email-OTP in front of every admin UI (Terraform `for_each` in `terraform/contabo/cloudflare.tf`). New admin UIs need a CF Access app + App Launcher entry.

## Repo-specific gotchas (verify against the code — they may have moved)

- **CI password generation must be alphanumeric only** (`setup_environment_ci.py`). A shell metachar like `&` in a generated secret breaks `airflow-init`.
- **kubeconform** must run with `-ignore-missing-schemas` (Traefik & other CRDs) — see `helm-validate.yml`.
- **Grafana v13 table panels**: migrate `custom.displayMode` → `custom.cellOptions` and bump `schemaVersion` to `39`, ship via Git→Argo. Only swap table-cell `custom.displayMode`; leave `legend.displayMode`/bargauge `displayMode` alone.
- **Neo4j Browser** blank/503 under load: set `NEO4J_server_threads_worker__count: "64"` in `helm/fuzeinfra/templates/databases.yaml` (+ CF cache rule for `/browser/assets/*`); Bolt over CF Tunnel uses the WebSocket endpoint (`NEO4J_server_bolt_advertised__address` → `neo4j-bolt.<domain>:443`).
- **Loki/Promtail config drift**: Loki needs `delete_request_store` when compaction/retention is on; Promtail `metric_relabel_configs` is invalid and must be removed (`configmaps-monitoring.yaml`).

## Service / port inventory

Databases: **Postgres** 5432 (+pgAdmin) · **MongoDB** 27017 (+Mongo Express) · **Redis** 6379 · **Neo4j** 7474/7687 · **Elasticsearch** 9200 · **ChromaDB** 8003 (vector). Messaging: **Kafka** 29092 (+Kafka UI, Zookeeper) · **RabbitMQ** 5672/15672. Network: **dnsmasq** 53 (UI 8053, `*.dev.local`) · **Consul** 8500/8600 · **nginx** reverse proxy · **Cloudflare tunnel**. Monitoring: **Prometheus** 9090 · **Grafana** 3001 · **Alertmanager** 9093 · **Loki** 3100 · **Promtail** · **node-exporter** 9100 · **kube-state-metrics** (k8s only). Workflow: **Airflow** 8082 (init/webserver/scheduler/worker) · **Flower** 5555.

## Project-integration guide (pointers)

Apps onboard onto the shared platform — they don't fork it. Mechanics live in the tooling, not here:

- **Connect**: `networks: { FuzeInfra: { external: true } }`; reach services by container name (`postgres`, `redis`, `mongodb`, …) via `DATABASE_URL`/`REDIS_URL`/`MONGODB_URL` env.
- **DNS / HTTPS**: `tools/dns-manager/dns-manager.py add <project>` (`*.dev.local`); `tools/cert-manager/setup-local-certs.sh` (mkcert).
- **Service discovery**: `tools/service-discovery/service-discovery.py register <project> --port … --health-check /health` (Consul).
- **Webhooks**: `tools/tunnel-manager/tunnel-manager.py register <project> github|atlassian …` + `webhook_sync.py` (auto-updates GitHub/Atlassian webhook URLs).
- **Env template**: `environment.template` (copy + inject). **Tests**: `python scripts-tools/run_tests.py` / `pytest tests/` (db, monitoring, messaging, workflow, web-UI; integration marked `@pytest.mark.integration`).

> Source every secret from `.env` / k8s Secrets — never hardcode. Verify by exercising it: `docker ps`, `kubectl -n fuzeinfra get pods`, curl through the ingress/tunnel, or the test suite. Finish work as a **merged PR** (baseline §8).
