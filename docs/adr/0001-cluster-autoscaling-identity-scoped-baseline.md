# ADR 0001 — Cluster autoscaling uses an identity-scoped floating baseline

**Status:** Accepted (2026-07-05)
**Context area:** FuzeInfra shared platform · Contabo k3s node autoscaling
**Related:** [`docs/superpowers/specs/2026-07-03-cluster-node-autoscaling-design.md`](../superpowers/specs/2026-07-03-cluster-node-autoscaling-design.md) · [`docs/INFRA_REQUEST_DISPATCH.md`](../INFRA_REQUEST_DISPATCH.md) · [`docs/runbooks/contabo-autoscaling-cutover.md`](../runbooks/contabo-autoscaling-cutover.md)

## Context

FuzeInfra is a **shared infrastructure platform**. It is deliberately **decoupled
from its consuming repos** (FuzeFront, mendyrobotics, …): consumers declare the
infrastructure they need — including worker VPS capacity — in *their own* repos and
dispatch it to FuzeInfra's `infra-request-handler` workflow, which provisions the
Contabo VPS (FuzeInfra is the sole credential holder) but stays **unaware of the
consumer's specifics** (their Cloudflare config, S3 buckets, or *why* they need the
node). This decoupling is the entire point of the platform.

We are adding node-level cluster autoscaling (upstream Cluster Autoscaler + a Contabo
`externalgrpc` provider). Autoscaling needs a notion of a **floor** — the set of nodes
that must never be scaled down. The naïve design modelled this as a **fixed count**: a
Terraform-managed "baseline pool" of N worker nodes (we initially picked 3), with the
autoscaler adding/removing nodes to keep the total at a target.

That naïve design is wrong for this platform, and we discovered why against live prod:

- Prod already runs **3 nodes**: 1 control-plane (TF-managed by FuzeInfra) **+ 2 workers
  that consumer repos provisioned out-of-band via the dispatch workflow** — nodes
  FuzeInfra's Terraform state knows nothing about.
- A Terraform-owned "provision 2 baseline workers to reach 3" step would therefore
  **(a)** create redundant VPS on top of the workers consumers already dispatched
  (extra cost, duplicate capacity), and **(b)** make FuzeInfra **own and track consumer
  capacity** — exactly the coupling the platform exists to avoid.
- The baseline is not a constant. It is **whatever consumers have dispatched so far** —
  3 today, maybe 4 or 5 tomorrow — and FuzeInfra must not try to predict or manage it.

## Decision

**The autoscaler scopes itself by identity (a tag), not by a count. The baseline is
implicit, dynamic, and owned by whoever created each node — never by the autoscaler.**

- FuzeInfra's Terraform (`terraform/contabo`) manages **exactly one** node: the
  control-plane VPS (`contabo_instance.prod`). It **never** provisions worker capacity
  on a consumer's behalf, and it **never** enumerates Contabo's account-wide instance
  list (no `data "contabo_instance"` / `for_each` over "all instances"). There is no
  `baseline.tf`.
- The Cluster Autoscaler manages **exactly one node group: the nodes it created**,
  tagged **`fuzeinfra-elastic`** (`min=0 … max=2`). The provider's `NodeGroupForNode`
  classifies any node **without** that tag as **foreign**: its capacity is counted for
  scheduling decisions, but it is **never a scale-down candidate**.
- Therefore the **floor is implicit and floating**: "all non-elastic nodes, whatever
  and however many." Control-plane, FuzeFront's worker, mendyrobotics' worker, and any
  node dispatched in the future are all foreign to the autoscaler with zero
  configuration. The elastic pool is purely **additive on top**: total = current
  baseline + up to 2 elastic. There is **no fixed "3-node baseline" and no "5-node
  ceiling."**

Corollary decisions:

- **Overlay networking is not touched by this feature.** Existing consumer workers
  already join over **public IP + Flannel VXLAN (`8472/udp`)**. Elastic nodes join the
  same way. A cluster-wide move to WireGuard would break those existing VXLAN nodes and
  is therefore a **separate, optional hardening initiative**, decoupled from autoscaling.
- The elastic-exclusion invariant is enforced two ways: **structurally** (FuzeInfra TF
  never broadly enumerates instances, and never tags a node `fuzeinfra-elastic`) and by
  a **CI guard** (`tf-elastic-drift-check.yml`) as a regression backstop.

## Alternatives considered

1. **Counted baseline (rejected).** A TF-managed fixed-size baseline pool + total-count
   target. Rejected: duplicates consumer-dispatched nodes, couples FuzeInfra to consumer
   capacity, and cannot express a baseline that changes when a consumer dispatches a node.
2. **FuzeInfra enumerates all Contabo instances and treats untagged ones as baseline
   (rejected).** Would make the floor dynamic, but requires FuzeInfra to read the whole
   account inventory — reintroducing awareness/coupling and creating a path by which
   `terraform apply` could adopt or destroy consumer/elastic nodes. Rejected on both
   decoupling and blast-radius grounds.
3. **Identity-scoped floating baseline (accepted).** No enumeration, no count, no
   coupling. The autoscaler knows only its own tag; everything else is someone else's
   node and is left untouched.

## Consequences

- **Positive:** FuzeInfra stays fully decoupled from consumer infrastructure. The
  autoscaler needs zero knowledge of who provisioned what. The baseline scales with
  consumer demand automatically. Cutover no longer requires a disruptive k3s-server
  overlay reinstall.
- **Positive:** Safety is structural — an untagged node can never be scaled down, so
  control-plane and consumer workers are protected by construction, not by a count that
  could drift.
- **Trade-off:** "How many nodes total?" has no static answer — it is `baseline + [0..2]`
  where baseline is observed at runtime, not declared. Dashboards/alerts must read the
  live node list rather than a configured constant.
- **Trade-off:** The elastic cap (`max=2`) bounds only the *additive* pool, not the total
  cluster size. If consumers dispatch many nodes, the cluster grows accordingly — that is
  by design (consumer-owned capacity), and cost governance for consumer nodes lives in the
  dispatch whitelist, not here.
- **Follow-up:** WireGuard overlay hardening is tracked separately and must account for
  all node classes (control-plane, consumer, elastic) at once if ever undertaken.
