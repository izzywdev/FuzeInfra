# Cluster Node Autoscaling (Contabo k3s) — Design

**Date:** 2026-07-03
**Status:** Approved design — ready for implementation planning
**Repo:** FuzeInfra
**Owner:** platform / devops

## 1. Goal & scope

Add **automatic horizontal cluster (node-level) autoscaling** to the FuzeInfra
production k3s cluster: provision additional Contabo VPS worker nodes when the
cluster is under load, and remove them when idle — using common, off-the-shelf
Kubernetes tooling rather than bespoke control logic.

**Primary driver:** handle load spikes (absorb bursts automatically, release
capacity when idle).

**In scope:**
- Node-level autoscaling of a dedicated **elastic worker pool** on Contabo.
- Hybrid scale-up trigger: unschedulable pods (hard) + proactive headroom.
- Autonomous scale-up **and** scale-down, bounded by caps and cooldowns.

**Explicitly out of scope:**
- **Control-plane HA.** The cluster remains single control-plane / non-HA. This
  feature adds *worker capacity*, not high availability. HA (odd number of
  `--server` nodes + HA datastore) is a separate future effort.
- **Stateful workload mobility.** `local-path` storage pins stateful services
  to the baseline; networked storage (e.g. Longhorn) is a separate prerequisite
  for freely moving stateful pods and is not addressed here.
- Pod-level autoscaling (HPA/VPA) — may be layered on later; not required for
  this feature.

## 2. Current state (starting point)

FuzeInfra already has the provisioning primitives; only the autoscaling control
loop is missing.

Already present:
- `modules/contabo-k3s-node/` — reusable Terraform module that provisions
  Contabo VPS agent nodes and joins them to k3s via cloud-init + node-token
  (`main.tf`, `cloud-init.tftpl`, `variables.tf`).
- `terraform/contabo/` — provisions the production control-plane VPS and installs
  k3s + ArgoCD (`vps.tf`, `provisioning.tf`), plus other infra (CF tunnels, DNS,
  secrets).
- `.github/workflows/infra-request-handler.yml` + `config/infra-request-whitelist.json`
  — a request-driven, whitelist-gated node provisioner (EU-only, `workload`
  role, `max_nodes_per_request: 3`).
- Full metrics stack: Prometheus, node-exporter, kube-state-metrics, cadvisor
  scrape (`helm/fuzeinfra/values.yaml`, `templates/monitoring.yaml`,
  `templates/kube-state-metrics.yaml`, `templates/configmaps-monitoring.yaml`).

Missing (this feature builds it):
- No HPA / VPA / Cluster Autoscaler / Karpenter / node pools.
- No `metrics-server` / `metrics.k8s.io` (not required for CA, which uses the
  scheduler's unschedulable-pod signal, not the metrics API).
- No autonomous load→node reconcile loop.

## 3. Key constraints (drive the whole design)

1. **Contabo is slow + monthly-billed.** VPS creation takes ~10–15 min; billing
   is monthly, not per-second. Reactions must be **coarse** (tens of minutes),
   not minute-by-minute elasticity. Real cost savings from scale-down only
   materialize when a node is torn down before its monthly renewal.
2. **Only stateless nodes can scale.** `local-path` storage pins stateful pods
   (Postgres, Kafka, Elasticsearch, Neo4j, Prometheus) to the node holding their
   data. The autoscaler may only add/remove **stateless-workload elastic nodes**
   and must never assume stateful pods reschedule.
3. **Elastic nodes join the same way existing consumer-dispatched workers
   do.** Prod already has worker nodes provisioned out-of-band by consumer
   repos (FuzeFront, mendyrobotics) via the `infra-request-handler` dispatch
   workflow, joining over public IP + Flannel VXLAN (`8472/udp`) — verified in
   prod. Elastic nodes use that exact same public-IP + VXLAN path; this
   feature does not touch the overlay network. (A cluster-wide move to
   WireGuard is a separate, optional hardening initiative, decoupled from
   autoscaling — see section 5.)
4. **Control plane is single-node / non-HA** (see scope exclusion).

## 4. Chosen approach

**Approach A′ — upstream Kubernetes Cluster Autoscaler + a thin Contabo
`externalgrpc` cloud provider + overprovisioning pause-pods.**

Rationale (build-vs-adopt): the standard, battle-tested tool is the Kubernetes
Cluster Autoscaler. It provides unschedulable-pod detection, PDB-aware graceful
drain, scale-down safety, cooldowns, and expander strategies for free. The only
bespoke code is a small gRPC "cloud provider" that maps CA's node-group
operations onto the Contabo API (reusing the existing module's cloud-init/join
recipe). The proactive half of the hybrid trigger is delivered idiomatically via
overprovisioning pause-pods rather than hand-rolled PromQL thresholds.

Rejected alternatives:
- **A (CA + gRPC, no pause-pods):** scale-up would be unschedulable-pods-only
  (purely reactive); no proactive headroom.
- **B (bespoke reconcile controller):** re-implements scale-down/drain/PDB safety
  that CA already provides; less battle-tested; cuts against "common tools."

## 5. Architecture — identity-scoped floating baseline, not a fixed count

FuzeInfra is a **shared platform**; consuming repos (FuzeFront, mendyrobotics)
are the ones who need worker capacity, and they request it in **their own**
repos by dispatching to FuzeInfra's `infra-request-handler` workflow, which
provisions the Contabo VPS (FuzeInfra holds the Contabo credentials) but stays
**unaware of the consumer's specifics** (their CF config, S3 buckets, why they
need it). FuzeInfra's Terraform (`terraform/contabo`) manages **exactly one**
node — the control-plane VPS (`contabo_instance.prod` in `vps.tf`) — and never
provisions worker capacity on a consumer's behalf. Prod today already has 3
nodes: 1 control-plane (TF-managed) + 2 workers dispatched out-of-band by
consumers (NOT in FuzeInfra's TF state). Tomorrow it may be 4 or 5 — FuzeInfra
does not track or predict that count.

The cluster autoscaler must therefore scope itself by **identity**, not by a
hardcoded total:

| | Baseline ("floating") | Elastic pool |
|---|---|---|
| Owner | Control-plane: **Terraform** (FuzeInfra). Workers: **consumer repos**, via dispatch | **Cluster Autoscaler** (live) |
| Provisioned by | `terraform/contabo` (control-plane only) + consumer-triggered `infra-request-handler` dispatch (workers) | CA → gRPC provider → Contabo API (reusing module cloud-init) |
| Size | **Implicit and dynamic** — whatever exists right now (control-plane + however many consumer workers have been dispatched; 3 nodes today) | `min=0 … max=2` **additional** nodes |
| Identity | anything NOT tagged `fuzeinfra-elastic` | tag `fuzeinfra-elastic` |
| In CA config? | **No** — CA never touches these; it only counts their capacity for scheduling | **Yes** — the only group CA scales |

- **There is no fixed "3-node baseline" and no "5-node ceiling."** The baseline
  is "all non-elastic nodes, whatever/however many" — it grows or shrinks only
  when a consumer repo dispatches a request, never via this Terraform. The
  elastic pool is purely **additive on top**: total nodes = current baseline +
  up to 2 elastic.
- CA is configured with *only* the elastic group. The provider's
  `NodeGroupForNode` classifies nodes by the `fuzeinfra-elastic` tag — every
  other node (control-plane, consumer workers, anything dispatched in the
  future) is "foreign": counted for scheduling capacity, **never a scale-down
  candidate**.
- **Decoupling principle:** Terraform in this directory never enumerates
  Contabo's account-wide instance list (no `data "contabo_instance"` /
  for_each over "all instances"). It owns only the named control-plane
  resource, so `terraform apply` can never adopt, diff against, or destroy a
  consumer-dispatched worker or an elastic node — there is no mechanism by
  which it could. This is what keeps FuzeInfra decoupled from consumer
  specifics: it provisions the VPS when asked, and stays unaware of everything
  else.

## 6. Components

Five pieces; only one is bespoke.

1. **Cluster Autoscaler (upstream, off-the-shelf).** Deployed via Helm into the
   `fuzeinfra` namespace. `--cloud-provider=externalgrpc`, one node group
   `elastic` (`min=0,max=2`). Tuned for Contabo:
   `--max-node-provision-time≈15m`, `--scale-down-unneeded-time≈30–60m`,
   `--max-total-unready-percentage` conservative. Owns the decision loop.

2. **Contabo `externalgrpc` provider (the ONE bespoke component).** A small Go
   gRPC service implementing CA's `CloudProvider` interface:
   - `NodeGroups()` / `NodeGroupForNode()` — reports the single `elastic` group;
     classifies nodes by the `fuzeinfra.io/pool=elastic` tag (baseline/control-
     plane read as "not mine").
   - `NodeGroupTargetSize()` — current count of elastic Contabo instances.
   - `IncreaseSize(n)` — create `n` Contabo VPS via API using the **reused
     cloud-init** from `modules/contabo-k3s-node` (K3S_URL, node-token,
     wireguard, elastic taint+label), tagged `fuzeinfra-elastic`. Refuses to
     exceed `max=2`.
   - `DeleteNodes([...])` — CA has already cordoned+drained; provider deletes
     those specific instances and removes them from k3s.
   - **Stateless:** truth is read from Contabo by tag; no local DB, no Terraform
     state to drift. On crash, k8s restarts it and CA reconnects.

3. **Overprovisioning / pause-pods (off-the-shelf pattern).** A Deployment of
   low-priority (negative `PriorityClass`) `pause` pods reserving ~1 node of
   headroom. Real workloads preempt them → pods go Pending → CA scales up
   *before* real workloads are stuck. This is the proactive half of the hybrid
   trigger. Replica count/size tunes how much spare capacity is kept.

4. **Scheduling guardrails (config).** Elastic nodes carry taint
   `fuzeinfra.io/elastic=true` + label; stateless app deployments get matching
   tolerations / `nodeAffinity`. Stateful services get
   `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"` so CA never tries to
   drain them. This is the safety spine that makes "only stateless nodes scale"
   hold in practice.

5. **Terraform boundary + overlay change (config).** Baseline TF config adds a
   tag / `lifecycle ignore` so it never adopts or destroys `fuzeinfra-elastic`
   instances; one-time switch of the k3s server to Flannel `wireguard-native`
   for public-IP node joins.

## 7. Data flows

**Scale-up:**
```
load rises → real pods preempt pause-pods → pause-pods Pending
  → CA sees unschedulable pods → CA.IncreaseSize(elastic, +1)
  → gRPC provider → Contabo API create VPS (cloud-init joins k3s, tainted)
  → node Ready (~10-15 min) → pause-pods reschedule → headroom restored
```

**Scale-down:**
```
utilization low for scale-down-unneeded-time (~30-60 min)
  → CA picks an elastic node, checks PDBs + safe-to-evict
  → CA cordons + drains (stateful pods excluded by annotation)
  → CA.DeleteNodes([node]) → gRPC provider → Contabo API delete VPS
  → floor respected: never below the current (floating) baseline — CA only
    ever deletes `fuzeinfra-elastic`-tagged nodes it created itself
```

## 8. Failure modes

| Failure | Handling |
|---|---|
| Slow provisioning (10–15 min) | `max-node-provision-time≈15m`; pause-pods pre-reserve headroom to absorb the lag. |
| Node never joins | Provider marks instance failed after timeout; CA retries; max-retry + backoff prevents thrash; tag-scoped reaper deletes orphaned instances. |
| Partial join (VM up, kubelet not Ready) | CA `unready` handling + `max-total-unready-percentage`; provider deletes nodes stuck unready past a deadline. |
| Terraform drift | Elastic instances tag-excluded from TF; CI check asserts no elastic-tagged instance appears in the TF plan. |
| Scale-down eviction risk | Stateful pods `safe-to-evict:false`; PDBs on stateless services; graceful drain; CA only ever deletes `fuzeinfra-elastic`-tagged nodes, so the floating baseline is never touched. |
| Monthly-billing waste | `scale-down-unneeded-time` coarse (30–60 min); documented that savings require living past the monthly renewal. |
| Runaway scale-up | Hard `max=2` cap enforced in the provider even if CA asks for more. |
| Control-plane SPOF | Out of scope; documented caveat. |
| gRPC provider crash | Stateless; k8s restarts; CA reconnects; no lost state. |

## 9. Testing strategy

- **Unit:** provider gRPC methods against a mocked Contabo API — IncreaseSize /
  DeleteNodes / NodeGroupForNode tag classification, cap enforcement (max=2),
  floor enforcement.
- **Integration (kind):** CA + provider against a fake cloud provider — assert
  unschedulable pods → IncreaseSize called; low utilization → DeleteNodes called;
  non-elastic (foreign) nodes never selected regardless of how many exist.
- **Staging e2e (real Contabo, gated):** one manual spike test — apply load,
  watch a real elastic node provision + join + drain + delete; verify the TF plan
  stays clean throughout.
- **Guardrail tests:** a stateful pod is never scheduled to / evicted from an
  elastic node; scale-down never deletes a node lacking the `fuzeinfra-elastic`
  tag, regardless of the current baseline count.

## 10. Prerequisites & sequencing notes

1. Elastic nodes join over the same public-IP + VXLAN (`8472/udp`) path the
   existing consumer-dispatched workers already use — no overlay-network
   change is required or in scope for this feature. (A cluster-wide move to
   WireGuard, if ever undertaken, is a separate hardening initiative.)
2. The baseline is whatever nodes exist at cutover time (today: control-plane
   + 2 consumer-dispatched workers = 3) — there is nothing to "bring to shape"
   in this Terraform, since FuzeInfra does not provision consumer worker
   capacity. The elastic pool is enabled against whatever baseline is current.
3. Contabo API credentials (OAuth2) already exist as CI secrets and will be
   consumed by the gRPC provider (via k8s Secret / SealedSecret) — reuse, do not
   duplicate.
4. All deploy wiring lands through GitOps (Helm values behind an `enabled` gate +
   Argo CD) — prod is never hand-deployed.

## 11. Open items to confirm during planning

- Exact Contabo `product_id` / `image_id` for elastic nodes (reuse whitelist-
  approved products).
- Whether the gRPC provider talks to the Contabo REST API directly (preferred,
  keeps it stateless) vs. shelling to Terraform (rejected — reintroduces state
  drift).
- Pause-pod sizing (how much proactive headroom to reserve).
- Whether the existing `infra-request` whitelist should also gate the elastic
  pool's product/region as a defense-in-depth check.
