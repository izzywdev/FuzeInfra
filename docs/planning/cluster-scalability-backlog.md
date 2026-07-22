# FuzeInfra scalable, self-healing cluster — backlog (Jira-ready)

> **Status:** drafted, **not yet in Jira.** Target site `fuzefront.atlassian.net`, SCRUM project
> **FuzeInfra** (key `FI`). Blocked on connecting that Atlassian site + Jira project-admin scope
> (the only connected Atlassian site is `phonedo.atlassian.net`, which is the wrong site and lacks
> project-create). Once connected this imports 1 Epic + 9 children as-is. Track here until then.
>
> After the `FI` project exists, its **FuzePlan workflow scheme / scripts** (statuses, screens,
> automation) are applied by the **FuzePlan** repo via cross-repo `@claude`, not from here.

## Epic — FuzeInfra scalable, self-healing cluster
Priority **High** · labels `autoscaling, ha, storage, networking, observability, k8s-version`

Elastic, identity-scoped autoscaling on Contabo k3s with a floating baseline, billing-aware
scale-down, shared storage, HA control plane, and full observability — decoupled from consumer
repos (FuzeFront, mendyrobotics).

| Ref | Summary | Type | Priority | Labels | In progress |
|-----|---------|------|----------|--------|-------------|
| A | Shared / networked storage (MinIO S3 and/or Longhorn) | Story | High | `storage` | no |
| B | Control-plane HA (≥3 servers + embedded etcd) | Story | High | `ha` | no |
| C | metrics-server + HPA/VPA | Story | Medium | `autoscaling, observability` | no |
| D | Billing-aware reaper: finish + verify live | Story | High | `autoscaling` | **yes** (PR #365) |
| E | Autoscaling observability (Grafana + alerts + cost) | Story | Medium | `observability, autoscaling` | no |
| F | WireGuard overlay: IaC reconciliation | Story | High | `networking` | **yes** |
| G | Standardize all nodes on one k8s LTS + fix version skew | Story | High | `k8s-version` | no |
| H | Elastic cloud-init WireGuard-compatible + version-pinned | Story | Highest | `networking, k8s-version, autoscaling` | **yes** (PR #364) |
| T18 | Contabo cluster-autoscaler cutover | Task | High | `autoscaling` | **yes** |

## Story details

### A — Shared / networked storage
Cluster is 100% node-local `local-path` today; no object or replicated storage, so stateful pods
can't move between nodes and elastic nodes can't host stateful workloads. Contabo offers
S3-compatible Object Storage the chart is half-wired for (`loki.s3.enabled`, path-style S3 block).
**AC:** a networked StorageClass and/or object store deployed via Helm/Argo; at least one stateful
workload validated moving/replicating across nodes.

### B — Control-plane HA
Single control-plane VPS = SPOF. **AC:** ≥3 k3s server nodes + HA datastore (embedded etcd);
cluster survives loss of one control-plane node.

### C — metrics-server + HPA/VPA
metrics-server not deployed; no pod-level autoscaling. **AC:** metrics-server live, `kubectl top`
works, at least one HPA driving a workload. (Node autoscaling — the CA — is T18; this is the pod tier.)

### D — Billing-aware reaper: finish + verify live *(in progress — PR #365)*
CA utilization scale-down disabled; a reaper CronJob releases an idle elastic node only within 24h
of its Contabo billing renewal (`createdDate + ~30d`), because Contabo cancel = end-of-billing.
**AC:** reaper deployed (Helm slice on #365), and a real idle elastic node released just before
renewal in prod. Open uncertainty flagged on #365: confirm the Contabo `createdDate` field + the
`billingPeriod≈720h` month approximation against a live API response.

### E — Autoscaling observability
No dashboard/alerts for elastic capacity. **AC:** Grafana dashboard + alert rules (elastic node
count, scale/reaper events, cost) shipped via Git→Argo.

### F — WireGuard overlay: IaC reconciliation *(in progress)*
The live prod cluster already runs flannel **`wireguard-native`** on all nodes (done by a parallel
session), but `terraform/contabo` does NOT declare it — drift. If the k3s server is ever
reprovisioned from IaC it reverts to VXLAN. **AC:** `--flannel-backend=wireguard-native` +
`51820/udp` codified in `terraform/contabo/provisioning.tf`/`vps.tf` and the
`modules/contabo-k3s-node` cloud-init, so reprovision keeps WireGuard; drift closed.

### G — Standardize on one k8s LTS + fix version skew
Live skew (2026-07-22): control-plane `v1.35.5+k3s1`, but `mendys-worker-1` and
`fuzeinfra-ci-runner-1` on `v1.36.2+k3s1` (workers NEWER than the control-plane = unsupported).
Nothing pins a version (elastic cloud-init used `channel=stable`, floating). **AC:** determine the
org-designated k8s LTS, pin `INSTALL_K3S_VERSION` cluster-wide (module + elastic cloud-init +
provisioning), reconcile the skewed workers, all nodes on one LTS version.

### H — Elastic cloud-init WireGuard-compatible + version-pinned *(in progress — PR #364)*
The elastic `deploy/elastic-userdata.template` was changed to drop `51820/udp` (matching the
pre-WireGuard module) and to use `channel=stable`; on the now-WireGuard cluster an elastic node
can't join the pod overlay (likely why instance 203458548 never joined) and the floating channel
feeds skew. **AC:** elastic cloud-init opens `51820/udp`, joins the WireGuard mesh, and pins the
control-plane k3s version; a real elastic node joins Ready. (Interim pin `v1.35.5+k3s1`; the true
LTS migration is G.)

### T18 — Contabo cluster-autoscaler cutover *(in progress)*
Autoscaler LIVE; scale-up proven (auth + real VPS create); 9 live bugs fixed. Remaining: deploy
Phase 1 (#364 naming + WireGuard/LTS cloud-init) + Phase 2 (#365 reaper + Helm), then a clean spike
proving create → tag → **join** → schedule, and cancel orphan 203458548 via `/cancel`.
**AC:** a full autoscaling cycle validated live.
