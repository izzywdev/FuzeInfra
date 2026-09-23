# Design: FuzeLB — an AWS-ALB-equivalent managed load balancer on Contabo VPS

Status: **Proposed (design only)** — this PR changes **no chart, no Terraform, no
running resource**. Nothing here is enabled, applied, or provisioned. Every build
step is gated on explicit approval of this document.

Author: design synthesis (Claude Code session `session_01LRgRZapd9VWyyWvLpGUVkU`)
Date: 2026-09-23
Scope (when built): `services/fuzelb-api/`, `helm/fuzeinfra/templates/fuzelb-*.yaml`,
`terraform/contabo/`, `cluster-autoscaler/contabo-externalgrpc/internal/contabo/`
(client reuse), `docs/consuming-repos/`, `tests/`.
Companion contract sketch: [`fuzelb-openapi.draft.yaml`](./fuzelb-openapi.draft.yaml)
(**illustrative, not frozen** — freezing is Milestone 1's deliverable).

---

## 0. Executive summary

**The product.** Contabo sells excellent cheap compute and has no managed layer-7
load balancer. Every Contabo customer who wants what AWS customers get from an
Application Load Balancer — a stable public endpoint, TLS termination, host/path
routing, health-checked backends, automatic failover, per-target metrics — builds it
themselves out of nginx and keepalived, badly, once per customer. **FuzeLB is that
layer, run as a service**: the customer calls an API (or the MCP surface, or the
console) and gets a highly-available L7 load balancer in front of their own Contabo
VPS fleet, with an AWS-shaped resource model they already know.

**The honest architecture.** FuzeLB is *not* a new datapath invention. The control
plane is a multi-tenant reconciler in the FuzeInfra cluster; the data plane is a pair
(or triple) of small Contabo VPS instances per tenant, each running **nginx** as the
proxy and **keepalived** for VIP ownership, with failover executed against the
**Contabo VIP API**. That exact failover mechanism is already running in this repo
for the k3s API endpoint (`helm/fuzeinfra/templates/api-vip-keepalived.yaml`,
verified live 2026-09-22, run 35718868309). FuzeLB generalizes a primitive we have
already proven in production, rather than betting on an unproven one.

**The constraint that shapes everything.** Contabo has **no create API for additional
IPs** — a VIP is a panel/order-flow purchase, verified and documented at
`terraform/contabo/api-floating-vip.tf`. So "create a load balancer" cannot be fully
synchronous the way `CreateLoadBalancer` is on AWS. The API is therefore designed
**async-first with an explicit `provisioning` state and an IP-pool model**: we hold a
pre-purchased inventory of additional IPs, allocate from it in milliseconds, and
refill the pool out of band. Every honest capacity statement in this design follows
from that one fact. §4.1.

**The identity model, stated plainly.** The user's framing is right and this design
adopts it: **FuzeInfra is kernel space, FuzeFront is user space, and FuzeInfra's
*public* API is an application that runs in user space.** There is no circularity —
there is a ring boundary crossed twice, exactly like a syscall. The in-cluster
control plane never authenticates a human, never knows what a tenant is, and never
calls FuzeFront on the request path. FuzeFront owns identity, tenancy, quota and
billing, and mints a short-lived scoped token that the kernel validates *statelessly*
against FuzeFront's JWKS. §6 is the full treatment, including how the kernel stays up
when user space is down.

**What ships with it.** The same operations are exposed three ways over one contract:
the REST API (§5), the FuzeInfra MCP server (§7) so an LLM agent can operate a load
balancer conversationally, and the FuzeFront console UI. The MCP surface is not a
side feature — an agent that can read its own LB's health and shift traffic is the
differentiator Contabo's incumbents do not have.

---

## 1. Why this, why now

| Force | Detail |
| --- | --- |
| **Gap in Contabo's catalog** | Contabo offers VPS, VDS, dedicated, object storage, private networking, additional IPs. It does **not** offer a managed LB. Hetzner does (Load Balancer, €5–30/mo); DigitalOcean does; Contabo customers migrating from AWS hit this wall immediately. |
| **We already run the hard parts** | VIP failover against the Contabo VIP API (verified), private VLAN 60932, the `contabo-externalgrpc` API client with OAuth2 password-grant + UUID `x-request-id` handling, Prometheus/Loki/Tempo, GitOps via Argo. FuzeLB is assembly, not research. |
| **It makes FuzeInfra's public API real** | FuzeInfra today exposes one narrow public-ish surface (`custom-hostname-api`, cluster-internal only). FuzeLB is the first true multi-tenant, externally-sold API, which forces the userspace/kernelspace boundary (§6) to be designed properly once, for everything that follows. |
| **Agent-native is an actual differentiator** | Exposing the same contract over MCP means a customer's Claude/GPT agent can diagnose a 502, read target health, and drain a bad backend. No commodity LB does this. |

**Non-goal:** competing with Cloudflare/AWS on global anycast. FuzeLB is regional,
single-DC-per-LB, and says so in its SLA (§10).

---

## 2. Scope: what "ALB-equivalent" means here

### 2.1 Parity matrix

| AWS ALB capability | FuzeLB v1 | Mechanism |
| --- | --- | --- |
| Stable DNS endpoint | ✅ | `<lb-id>.lb.fuzefront.com` A-record(s) → allocated VIP(s) |
| HTTP/HTTPS listeners | ✅ | nginx `server` blocks |
| TLS termination | ✅ | nginx `ssl_certificate`; ACME (Let's Encrypt, DNS-01 + HTTP-01) or customer-uploaded PEM |
| Host-based routing | ✅ | nginx `server_name` |
| Path-based routing | ✅ | `location` with prefix/exact/regex match |
| Header / method / query-string routing | ✅ | `map` + `if`-free rule compilation |
| Target groups | ✅ | nginx `upstream` blocks |
| Active health checks | ✅ | separate health-prober process (see §3.4) — **not** nginx OSS, which has no active checks |
| Passive health checks / outlier ejection | ✅ | `max_fails` / `fail_timeout` |
| Round-robin / least-conn / IP-hash | ✅ | nginx `upstream` LB methods |
| Sticky sessions | ✅ | cookie-based (`sticky` via `map`-keyed consistent hash; see §3.3 note) |
| Connection draining / deregistration delay | ✅ | target `state=draining` → weight 0, existing conns allowed to finish |
| HTTP→HTTPS redirect, fixed responses | ✅ | rule `action.type: redirect \| fixed_response` |
| WebSocket / HTTP/2 | ✅ | `proxy_http_version 1.1` + `Upgrade`; `http2 on` |
| gRPC | ✅ | `grpc_pass` |
| Access logs | ✅ | JSON log → Promtail → Loki |
| Metrics | ✅ | nginx exporter → otel-collector → Prometheus; per-LB Grafana dashboard |
| Request tracing | ✅ | OTel nginx module, `traceparent` injected/propagated → Tempo |
| Cross-zone LB | ➖ | N/A: Contabo DCs are not AZs. Multi-DC is a v2 topic (§12). |
| WAF | ➖ v1.5 | ModSecurity + OWASP CRS behind a feature gate; not v1. |
| Mutual TLS to clients | ➖ v2 | `ssl_verify_client` is trivial; the CA lifecycle is not. |
| Network (L4/TCP/UDP) LB | ➖ v1.5 | nginx `stream` module; separate resource type `network_load_balancer`. |
| IPv6 | ✅ | Contabo VPS ship /64s; listener `ip_version: dualstack`. |

### 2.2 Explicit non-goals for v1

- No global anycast, no multi-region failover (§12 lists what would be needed).
- No serverless/Lambda targets — targets are IP:port or a FuzeInfra-internal Service.
- No autoscaling of *customer* backends. FuzeLB reports health; it does not scale.
- **No customer-supplied nginx config fragments.** The config is compiled from the
  declarative spec only. Accepting raw nginx snippets in a multi-tenant proxy is a
  remote-code-execution surface (`perl_set`, `lua_*`, `proxy_pass` to link-local
  metadata) and there is no safe subset worth the support cost. Escape hatches are
  added as first-class spec fields, never as passthrough.

---

## 3. Architecture

### 3.1 The two planes

```
                     ┌──────────────────────── USER SPACE ───────────────────────────┐
  customer browser   │  FuzeFront                                                     │
  customer CI ──────►│   • OIDC login / session      • tenant + org model             │
  customer agent     │   • quota + entitlement       • billing / metering             │
       (MCP)         │   • mints scoped LB token (aud=fuzeinfra-lb, JWKS-signed)      │
                     │   • public edge: api.fuzefront.com/lb/*  ·  mcp-lb.<domain>    │
                     └───────────────────────────┬───────────────────────────────────┘
                                                 │  short-lived scoped JWT  (§6)
                     ┌───────────────────────────▼──────── KERNEL SPACE ──────────────┐
                     │  FuzeInfra cluster, ns fuzeinfra                                │
                     │  ┌──────────────┐   watch/CRUD   ┌───────────────────────────┐  │
                     │  │ fuzelb-api   │◄──────────────►│ Postgres  (lb_* tables)   │  │
                     │  │ (FastAPI)    │                └───────────────────────────┘  │
                     │  │ stateless    │                                               │
                     │  └──────┬───────┘   desired state (generation N)                │
                     │         │                                                       │
                     │  ┌──────▼─────────────┐   Contabo API   ┌────────────────────┐  │
                     │  │ fuzelb-reconciler  │────────────────►│ instance create /  │  │
                     │  │ (leader-elected)   │                 │ VIP assign / tags  │  │
                     │  └──────┬─────────────┘                 └────────────────────┘  │
                     │         │ render + push config                                  │
                     └─────────┼───────────────────────────────────────────────────────┘
                               │ mTLS over the Contabo private VLAN (60932)
   ┌───────────────────────────▼─────────────────────────────────────────────────────┐
   │  DATA PLANE — per-tenant Contabo VPS pair (tagged fuzelb:<lb-id>)                │
   │                                                                                  │
   │   node A  (holds VIP-A, MASTER)          node B  (holds VIP-B, MASTER)           │
   │   ├─ nginx            ─ the proxy        ├─ nginx                                │
   │   ├─ fuzelb-agent     ─ config pull,     ├─ fuzelb-agent                         │
   │   │                     atomic reload    │                                       │
   │   ├─ fuzelb-prober    ─ active health    ├─ fuzelb-prober                        │
   │   ├─ keepalived       ─ VRRP unicast     ├─ keepalived                           │
   │   │                     over eth1        │                                       │
   │   └─ node_exporter + nginx exporter      └─ …                                    │
   └──────────────────────────────────────────────────────────────────────────────────┘
        DNS: <lb-id>.lb.fuzefront.com → A VIP-A, A VIP-B   (round-robin, DNS-only)
```

**Active-active, not active-passive.** Both nodes serve. Each owns one VIP; the LB's
hostname has an A record per VIP, so clients round-robin across live nodes. If a node
dies, keepalived on the survivor adopts the dead node's VIP and reassigns it through
the Contabo VIP API, so the published A record never resolves to a black hole. This
is exactly the topology and the code path already proven for `api.prod.fuzefront.com`
(`docs/runbooks/api-floating-vip.md`); FuzeLB parameterizes it per tenant instead of
hard-coding one cluster's nodes.

**Why VPS and not a k8s Service.** The data plane deliberately does *not* run in the
FuzeInfra cluster. Three reasons: (a) blast radius — a customer's traffic spike must
not evict FuzeInfra's own workloads; (b) tenancy — a shared ingress means a shared
nginx worker pool and a shared TLS session cache, which is a cross-tenant boundary we
would rather not have to defend; (c) Contabo's ingress story for the cluster is
tunnel-only by design (Traefik pinned to ClusterIP,
`argocd/cluster-bootstrap/traefik-clusterip.yaml`) — putting customer ingress there
would mean unpinning it, which breaks a load-bearing security invariant of this repo.

### 3.2 Control-plane components

| Component | Runs | Responsibility |
| --- | --- | --- |
| `fuzelb-api` | Deployment, ns `fuzeinfra`, 2 replicas, **no Ingress** | Validates + persists desired state. Stateless. Authorizes every request (§6.4). Returns immediately; never blocks on Contabo. |
| `fuzelb-reconciler` | Deployment, 1 replica + lease-based leader election | The only thing that talks to Contabo or to data-plane nodes. Drives `desired generation` → `observed generation` per LB. Idempotent, resumable, rate-limited. |
| `fuzelb-ipam` | Library inside the reconciler + a CronJob | Allocates VIPs from the pre-purchased pool; emits `fuzelb_ip_pool_available` so a low pool pages *us*, not the customer (§4.1). |
| `fuzelb-acme` | CronJob | ACME order/renew for managed certificates; writes into `lb_certificates`; never touches a customer-uploaded key beyond storing it sealed. |
| Postgres `fuzelb` DB | existing cluster Postgres | Source of truth. Provisioned by the existing `service-db-provisioning.yaml` path. |

**Every one of these is behind an `enabled` gate in `helm/fuzeinfra/values.yaml`
(`fuzelb.enabled: false` by default) and must be wired into `values-local.yaml`,
`values-aws.yaml` and `values-contabo.yaml`** — the repo's standing rule.

### 3.3 Config compilation: spec → nginx

The reconciler compiles the whole LB to a **single rendered bundle**, never a diff:

```
desired spec (DB) ──► validate ──► render (Jinja2) ──► bundle.tar.gz
                                                        ├─ nginx.conf
                                                        ├─ conf.d/<listener>.conf
                                                        ├─ certs/<cert-id>.{crt,key}
                                                        ├─ probes.json   (for fuzelb-prober)
                                                        └─ manifest.json (generation, sha256)
```

The agent on each node pulls the bundle over mTLS, writes it to
`/etc/fuzelb/staging/<generation>/`, runs `nginx -t -c staging/nginx.conf`, and only
on success flips the `current` symlink and issues `nginx -s reload`. **A bundle that
fails `nginx -t` is never made live**, and the agent reports `config_apply_failed`
with the validator's stderr back to the reconciler, which surfaces it on the LB's
`status.conditions` — the customer sees *why* their rule was rejected, not a silent
no-op. Reload is SIGHUP: in-flight requests on old workers complete.

Generations are monotonic. The agent refuses to apply a bundle older than the one it
is running, so a delayed retry can never resurrect a rolled-back config.

*Sticky-session note:* nginx OSS has no `sticky cookie` directive (that is NGINX
Plus). v1 implements affinity as a signed cookie carrying the consistent-hash key,
mapped to an upstream via `map $cookie_fuzelb_aff $backend`. It is honest about the
limit: affinity survives a reload, and breaks if the chosen target leaves the pool.

### 3.4 Health checking

nginx OSS has no active health checks, so `fuzelb-prober` is a real component, not a
wrapper:

- Probes each target per its target-group config (`protocol`, `path`, `port`,
  `interval`, `timeout`, `healthy_threshold`, `unhealthy_threshold`, `matcher`).
- On a state change it rewrites **only** `conf.d/upstreams.map` and reloads — an
  unhealthy target is written with `down`, a draining target with `weight=0`.
- Publishes `fuzelb_target_health{lb,tg,target}` and the transition reason, so the
  API's `GET /target-groups/{id}/health` reads live truth, and Grafana/Alertmanager
  see the same signal.
- Two-layer defence: passive ejection (`max_fails`/`fail_timeout`) catches failures
  between probe intervals; the prober catches failures a live request never touches.

**The prober is per-node, not central.** Node A and node B probe independently, so a
target reachable from one node and not the other is correctly degraded on one node
only — a central prober would make a network partition look like a target failure.

### 3.5 Provisioning a load balancer (the real sequence)

```
POST /load-balancers                       → 202, state=provisioning
  ├─ IPAM: allocate 2 VIPs from the pool               (~ms; §4.1)
  ├─ Contabo createInstance × 2                        (~2–5 min)
  │    productId per the tenant's size class, region = tenant's region,
  │    addOns.privateNetworking {}      ← MANDATORY, see §4.2
  │    tags = [fuzelb, fuzelb:<lb-id>, tenant:<tenant-id>]
  │    userData = cloud-init: agent + nginx + keepalived + prober, joined to
  │               the reconciler by a one-time bootstrap token
  ├─ assign each instance to private network 60932     (post-create, eventual)
  ├─ VIP assign: POST /v1/vips/{ip}/instances/{id}     (plural "instances"; body {})
  ├─ publish DNS: A <lb-id>.lb.fuzefront.com → each VIP (DNS-only, TTL 60)
  ├─ push generation 1 bundle                          → nginx up
  └─ first successful probe                            → state=active
```

Median expected time to `active`: **3–6 minutes**, dominated by Contabo instance
creation. The API says so in `estimated_ready_at` rather than pretending it is
instant.

### 3.6 Observability

Per-LB, shipped by default, no customer configuration:

- **Metrics** — nginx exporter → otel-collector (`:4317`) → Prometheus. Series:
  `fuzelb_requests_total{lb,listener,rule,status}`,
  `fuzelb_request_duration_seconds` (histogram, for p50/p95/p99),
  `fuzelb_upstream_duration_seconds`, `fuzelb_active_connections`,
  `fuzelb_target_health`, `fuzelb_tls_handshakes_total{version}`,
  `fuzelb_cert_expiry_seconds`.
- **Logs** — nginx JSON access log → Promtail → Loki, labelled `lb_id`, `tenant_id`.
  Retention per plan; the log line carries `trace_id` so Loki↔Tempo click-through
  works, per `docs/consuming-repos/OBSERVABILITY_DASHBOARDS.md`.
- **Traces** — the OTel nginx module injects `traceparent` when absent and propagates
  it when present, so a customer's backend spans join the LB span. The LB is usually
  the first hop, which makes it the natural trace root.
- **Dashboard** — one Grafana dashboard per LB, delivered by the existing
  `grafana_dashboard: "1"` sidecar label, scoped by `lb_id` template variable.
  Exposed to the customer read-only through FuzeFront (never raw Grafana).

Apps send to the collector, never to Tempo/Loki directly — the standing rule.

---

## 4. Contabo-specific constraints (the things that will bite)

### 4.1 Additional IPs cannot be created by API — this is the design's hard edge

`terraform/contabo/api-floating-vip.tf` records it plainly: *"It does NOT order the
IPs — that is a panel purchase (Contabo has no create API for additional IPs,
verified)."* There is no `POST /v1/vips`.

Consequences, and the design's answer:

1. **Pool model, not just-in-time.** We maintain a pre-purchased inventory of
   additional IPs per region in a `lb_ip_pool` table (`state`: `free | allocated |
   quarantined`). Allocation is a row update. `POST /load-balancers` fails fast with
   `503 capacity_unavailable` and a `Retry-After` if the pool is dry — an honest
   error beats a 40-minute provisioning hang.
2. **Pool depth is an operational SLO, not a background chore.**
   `fuzelb_ip_pool_available{region}` with a page at `< 10` and a warning at `< 25`.
   Refill lead time is a human purchase; the alert threshold must exceed the worst
   observed lead time × peak daily signups. **This is the single most likely cause of
   a public capacity outage** and it is owned by us, not by the customer.
3. **Released IPs are quarantined, not reused immediately.** A returned IP goes to
   `quarantined` for ≥ 24h so the previous tenant's stale DNS caches cannot deliver
   traffic to a new tenant. Skipping this is a cross-tenant traffic leak.
4. **If Contabo later ships a VIP-order endpoint, only `fuzelb-ipam` changes.** The
   pool abstraction is deliberately the only thing that knows.

### 4.2 Private networking: order the add-on at create, always

Per this repo's standing rule (`docs/design/off-vlan-node-failure-policy.md` §2a): a
node off the VLAN is not a cheaper node, it is a broken one. For FuzeLB the VLAN
carries (a) VRRP unicast between the pair — public IPs are not a shared L2 segment,
so VRRP has no other path — and (b) the mTLS config channel from the reconciler.

So: `createInstance` with `addOns: {privateNetworking: {}}` **unconditionally**, then
assign to the network after the instance becomes visible. Both steps are required —
the add-on grants the paid capability, the assign grants membership. An `/upgrade`
call without the add-on returns HTTP 402. **A FuzeLB node born off-VLAN is failed and
recreated, never "joined anyway."**

### 4.3 Contabo API behaviours already encoded in this repo — reuse, don't rediscover

`cluster-autoscaler/contabo-externalgrpc/internal/contabo/client.go` and the
keepalived `notify.sh` already carry the field-verified truths. FuzeLB's reconciler
**imports that client** rather than writing a second one:

- OAuth2 **password grant** against `auth.contabo.com/.../token` (client id/secret
  **and** API user/password — four credentials, not two).
- Every request needs a **UUID** `x-request-id`; a non-UUID returns HTTP 400.
- `userData` is **plain** cloud-config text, never base64 (this caused a live
  incident).
- VIP holder is `GET /v1/vips/{ip}` → `.data[0].resourceId` — **top-level**, not
  inside `assignments[]`.
- The VIP path segment is the **plural** `instances`; `instance` is rejected.
- VIP assign **requires a body**: `-d '{}'`, else 400 "Body cannot be empty".
- Reassign = `DELETE` unassign from the old holder, then `POST` assign to the new.
- **There is no DELETE-instance API** and **no API reversal of a cancellation**;
  cancel is end-of-billing-period, not immediate. So LB deletion returns the VIP to
  the pool and the *instances* enter a reaper queue with a cancellation request — the
  customer's billing stops at delete, ours stops at period end. That delta is a real
  cost-of-goods line, not a rounding error, and §9 prices it in.
- The API is eventually consistent; every read-after-write retries with backoff.

### 4.4 Single-DC blast radius

One LB's pair lives in one Contabo region. A DC-level outage takes the LB down.
Saying "multi-AZ" would be a lie. The SLA (§10) commits to **node-level** HA and is
explicit that region-level redundancy requires two LBs plus customer-side DNS
failover — which the API supports (§5.6) but does not manage in v1.

---

## 5. The customer API

Base URL (public, user space): `https://api.fuzefront.com/lb/v1`
Kernel-side service (never public): `http://fuzelb-api.fuzeinfra.svc.cluster.local:8080`

Design rules: resource-oriented, AWS-shaped nouns so the mental model transfers,
**async-first with observable state**, idempotent creates, cursor pagination,
RFC 9457 problem responses, and `ETag`/`If-Match` on every mutation.

### 5.1 Resource model

```
LoadBalancer  1─┬─n Listener   1─┬─n Rule ──► action ──► TargetGroup
                │                └─ certificates[]           │
                └─n (VIPs, DNS name, state)                  └─1─n Target
```

### 5.2 Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/load-balancers` | List (cursor paginated, filter `state`, `region`, `tag`) |
| `POST` | `/load-balancers` | Create → `202` + `state=provisioning` |
| `GET` | `/load-balancers/{lbId}` | Get, incl. `status.conditions[]` |
| `PATCH` | `/load-balancers/{lbId}` | Rename, retag, resize (size class) |
| `DELETE` | `/load-balancers/{lbId}` | Delete → `202`, `state=deleting` |
| `GET` | `/load-balancers/{lbId}/events` | Lifecycle + config-apply audit trail |
| `GET` | `/load-balancers/{lbId}/metrics` | Time-series (range, step, series selector) |
| `GET` | `/listeners` · `POST` `/load-balancers/{lbId}/listeners` | List / create listener |
| `GET`/`PATCH`/`DELETE` | `/listeners/{listenerId}` | Manage listener |
| `GET`/`POST` | `/listeners/{listenerId}/rules` | Routing rules (ordered by `priority`) |
| `GET`/`PATCH`/`DELETE` | `/rules/{ruleId}` | Manage rule |
| `PUT` | `/listeners/{listenerId}/rules:reorder` | Atomic reprioritize (avoids N racing PATCHes) |
| `GET`/`POST` | `/target-groups` | List / create target group |
| `GET`/`PATCH`/`DELETE` | `/target-groups/{tgId}` | Manage target group |
| `POST` | `/target-groups/{tgId}/targets:register` | Register targets (batch) |
| `POST` | `/target-groups/{tgId}/targets:deregister` | Deregister (honours drain delay) |
| `GET` | `/target-groups/{tgId}/health` | Live per-target health + last reason |
| `GET`/`POST` | `/certificates` | List / upload or order (ACME) a certificate |
| `GET`/`DELETE` | `/certificates/{certId}` | Inspect (never returns the private key) / delete |
| `GET` | `/quotas` | Tenant's limits and current usage |
| `GET` | `/regions` | Regions + **live** capacity signal (§4.1) |
| `GET` | `/healthz` · `/readyz` | Service liveness (unauthenticated, kernel-side only) |

### 5.3 Create, concretely

```http
POST /lb/v1/load-balancers
Authorization: Bearer <FuzeFront-issued JWT>
Idempotency-Key: 4f1c...   # required on POST; replay returns the original response
Content-Type: application/json

{
  "name": "prod-edge",
  "region": "EU",
  "size": "small",                     // small | medium | large  (§9)
  "ip_version": "dualstack",
  "listeners": [{
    "protocol": "HTTPS", "port": 443,
    "certificate_ids": ["cert_01J..."],
    "default_action": { "type": "forward", "target_group_id": "tg_01J..." },
    "rules": [{
      "priority": 10,
      "conditions": [
        { "field": "host",        "values": ["api.example.com"] },
        { "field": "path-prefix", "values": ["/v2/"] }
      ],
      "action": { "type": "forward", "target_group_id": "tg_01J...v2" }
    }]
  }],
  "tags": { "env": "prod" }
}
```

```http
HTTP/1.1 202 Accepted
Location: /lb/v1/load-balancers/lb_01JKX7...
ETag: "gen-1"

{
  "id": "lb_01JKX7...",
  "state": "provisioning",
  "dns_name": "lb-01jkx7.lb.fuzefront.com",
  "addresses": [],                       // populated when VIPs bind
  "estimated_ready_at": "2026-09-23T10:41:00Z",
  "status": { "conditions": [
    { "type": "IpAllocated",    "status": "True"  },
    { "type": "NodesReady",     "status": "False", "reason": "InstanceCreating" },
    { "type": "ConfigApplied",  "status": "False", "reason": "WaitingForNodes" }
  ]}
}
```

`state` ∈ `provisioning | active | updating | degraded | failed | deleting | deleted`.
`degraded` is a first-class state (one node down, LB still serving) — collapsing it
into `active` or `failed` would make the customer's own alerting wrong.

### 5.4 API semantics that are not optional

- **Idempotency.** `Idempotency-Key` required on every `POST`; the key + request
  digest is stored for 24h. Replaying with the same key and a *different* body is
  `422`, never a silent second load balancer.
- **Optimistic concurrency.** Every mutation takes `If-Match: <etag>`; a stale ETag
  is `412`. Two agents editing the same listener cannot silently clobber each other.
- **Errors** are RFC 9457 problem documents with a stable machine `code`
  (`quota_exceeded`, `capacity_unavailable`, `invalid_rule`, `certificate_expired`,
  `target_unreachable`, …) and, for config rejections, the nginx validator's message
  verbatim in `detail`.
- **Rate limits** per tenant: 60 writes/min, 600 reads/min, surfaced as
  `RateLimit-*` headers (RFC 9331 style) and enforced in user space (FuzeFront),
  where the tenant identity lives.
- **Pagination** is cursor-based (`page[cursor]`, `page[size]`, max 200) — offsets
  skip rows under concurrent writes.
- **Versioning** is in the path (`/v1`). Breaking changes require a new major and
  `oasdiff` in CI per the repo's `versioning` standard.

### 5.5 Contract-first, and gated

The OpenAPI document is the source of truth and is **frozen before any
implementation** (repo standard: `api-contract-first`). `tests/test_fuzelb_contract.py`
asserts the running app's generated schema matches the committed spec byte-for-byte,
so drift fails CI — the same guard `custom-hostname-api` already uses. A typed client
(`@fuzefront/fuzelb-client`) is generated from it, so the console, the MCP server and
the tests all build against one artifact.

### 5.6 DNS failover hook (the multi-region escape hatch)

`GET /load-balancers/{lbId}` returns `addresses[]`. A customer wanting cross-region
redundancy creates two LBs and points a health-checked DNS record at both. We publish
the health endpoint; we do not manage the record. Saying so explicitly is better than
implying a redundancy we do not deliver.

---

## 6. AuthN / AuthZ — the userspace/kernelspace ring model

### 6.1 The framing, and why it is not circular

The user's formulation: *FuzeFront is logically a layer on top of FuzeInfra, and
FuzeInfra's public API is logically an app layer on top of FuzeFront — userspace vs
kernelspace.* That is exactly right, and the apparent circularity dissolves the same
way it does in an operating system:

- **The kernel provides mechanism.** FuzeInfra runs compute, storage, networking,
  and now load balancing. It has **no concept of a user, a tenant, a plan or an
  invoice.** It authorizes a *bearer of a capability*, not a person.
- **User space provides policy.** FuzeFront owns login, organizations, tenancy,
  entitlement, quota, billing and the console. It is the only thing that can answer
  "is this human allowed to do this, on whose behalf, and who pays."
- **The public API is an application.** `api.fuzefront.com/lb/*` is a FuzeFront
  application that calls the kernel's private interface. It is FuzeLB's `libc`.
- **The boundary crossing is a syscall.** A FuzeFront-minted, short-lived, scoped,
  audience-bound JWT is the trap instruction. The kernel validates it *statelessly*.

FuzeFront runs **on** FuzeInfra (it is a workload in the cluster) and sits **above**
it (it fronts the kernel's API). A process runs on the kernel and calls into it;
nothing is circular about that. What would be circular — and is forbidden — is the
kernel making a **synchronous request-path call into user space to authorize**. It
never does. §6.5.

### 6.2 The token

FuzeFront authenticates the human or machine by its own means (OIDC via the auth
proxy; per `docs/design/realtime-messaging-and-edge-hardening.md` §4.3 FuzeFront
consumes the IdP token, never forwards it, and signs its own). It then mints a
**capability token** for the kernel:

```jsonc
{
  "iss": "https://api.fuzefront.com",       // FuzeFront, not the IdP
  "sub": "usr_01J...",                      // FuzeFront user UUID (never the IdP sub)
  "aud": "fuzeinfra-lb",                    // audience-bound: useless at any other service
  "act": { "sub": "svc_console" },          // actor, when acting on behalf of a user
  "tenant": "ten_01J...",                   // THE authorization boundary
  "scope": "lb:read lb:write lb:cert:write",
  "res": ["lb_01JKX7...", "tg_01J..."],     // optional: downscope to named objects
  "jti": "...", "iat": ..., "exp": ...       // exp ≤ 300s
}
```

- Signed with FuzeFront's own keys; **validated against FuzeFront's JWKS**, never the
  IdP's. This is what keeps the IdP swappable (Authentik → Keycloak) without touching
  the kernel.
- `exp ≤ 5 min`. A leaked LB token is a five-minute problem, and refresh lives in
  user space where sessions and device registries already are.
- `aud` pinning means a token for FuzeLB cannot be replayed against the A2A gateway,
  the custom-hostname API, or anything else. Every kernel service gets its own `aud`.
- `jti` is recorded for audit and for break-glass revocation of a specific token.

### 6.3 Machine callers (CI, a customer's agent)

Two supported shapes, both ending in the same token:

1. **OAuth2 client credentials** against FuzeFront (`client_id`/`client_secret` per
   service account, scoped at issue time). Standard, revocable, per-tenant.
2. **mTLS client certificate** for high-assurance callers, exchanged for the same JWT.

At the edge, machine traffic additionally passes a **Cloudflare Access service-token**
policy — the pattern already used for `handoff-mcp` (CF Access "bypass" app in front
so the app bearer is the real gate) and modelled in Terraform's `for_each` over CF
Access apps. Human console traffic passes CF Access email-OTP. Neither replaces the
JWT; they are an outer envelope that keeps unauthenticated traffic off the origin.

### 6.4 What the kernel actually enforces

`fuzelb-api` performs, on **every** request, in this order:

1. **Authenticate** — verify signature against cached JWKS, check `iss`, `aud`, `exp`,
   `nbf`. Missing/invalid → `401`. (Pattern: `custom-hostname-api`'s `auth.py`, where
   the comment is already the right one: *the token is not merely an authentication
   credential — it is the authorization boundary.*)
2. **Authorize the verb** — required scope per operation; `lb:read` cannot `POST`.
   Missing scope → `403`.
3. **Authorize the object** — **every** row read or written is filtered by
   `tenant_id = claims.tenant` **in the query**, not checked after fetch. This is the
   BOLA/IDOR mitigation the repo's `endpoint-authorization` skill and the `gate-authz`
   check require. An ID belonging to another tenant returns `404`, never `403` —
   `403` confirms the object exists.
4. **Authorize the field** — plan-gated fields (WAF, log retention, size class) are
   rejected, not silently dropped, when the entitlement claim does not carry them.
5. **Validate** — strict schema; unknown fields rejected. No raw nginx passthrough
   (§2.2), no `proxy_pass` to `169.254.0.0/16`, `127.0.0.0/8` or the cluster's own
   service CIDR — an SSRF guard enforced at validation *and* at render.
6. **Audit** — `(jti, sub, act, tenant, operation, object, decision, generation)` to
   the append-only `lb_audit` table and to Loki. Every config generation is
   attributable to a token.

Tenant isolation is defence-in-depth, not a single `WHERE`: separate Postgres row
ownership, separate Contabo instances and tags, separate VIPs, separate nginx
processes, separate Grafana scoping. A control-plane authz bug cannot produce
cross-tenant *traffic*.

### 6.5 The circularity break — kernel liveness must not depend on user space

This is the rule that makes the layering safe rather than cute:

- **JWKS is cached** with a long stale-if-error window (24h). If FuzeFront is down,
  already-issued tokens still validate. The kernel degrades to "no *new* logins,"
  not "no load balancing."
- **The data plane never calls the control plane to serve traffic.** nginx serves from
  its last-applied bundle. FuzeFront down, `fuzelb-api` down, Postgres down, the whole
  cluster down — **customer traffic keeps flowing.** Only *changes* stop. This is the
  single most important availability property in the design, and it is why config is
  a pushed bundle rather than a runtime lookup.
- **Break-glass static bearer.** A SealedSecret-delivered static token, scoped
  `lb:admin`, accepted by `fuzelb-api` only from inside the cluster, used solely to
  bootstrap the first tenant and to operate during a FuzeFront outage. Its use is
  loudly audited and alerted (`fuzelb_breakglass_auth_total > 0` pages).
- **No synchronous kernel→FuzeFront call on the request path.** Quota and entitlement
  arrive *in the token claims*, not by callback. Usage flows back **asynchronously**
  via the metering pipeline (§9). The kernel therefore has no runtime dependency on
  user space in either direction.

### 6.6 Network posture

`fuzelb-api` has **no Ingress and no tunnel route** — cluster-internal only, plus a
NetworkPolicy admitting the FuzeFront namespace and the MCP pod. Identical to the
`custom-hostname-api` posture, for the same reason: the only public door should be the
one in user space, where identity lives.

---

## 7. MCP surface

FuzeInfra's MCP server exposes the same frozen contract as tools, so an agent can
operate a load balancer in natural language. The deployment pattern already exists in
this repo (`helm/fuzeinfra/templates/handoff-mcp.yaml`): tunnel-exposed at
`mcp-lb.<domain>`, a more-specific CF Access *bypass* app in front so the app bearer
is the gate, bearer delivered as a SealedSecret, enabled by values in the same change
that lands the secret.

### 7.1 Tools

| Tool | Scope required | Notes |
| --- | --- | --- |
| `lb_list` / `lb_get` | `lb:read` | Includes `status.conditions` so an agent can explain *why* something is degraded |
| `lb_create` | `lb:write` | Returns the async handle; the agent is told to poll, not to assume |
| `lb_update` / `lb_delete` | `lb:write` | `lb_delete` requires `confirm: true` **and** the LB's exact name — a destructive op must not be one fuzzy token away |
| `listener_*`, `rule_*` | `lb:write` | `rules_reorder` is atomic (§5.2) |
| `target_group_*` | `lb:write` | |
| `targets_register` / `targets_deregister` | `lb:write` | Deregister honours drain delay |
| `target_health` | `lb:read` | The diagnosis workhorse |
| `lb_metrics` | `lb:read` | Bounded range + step; returns downsampled series, never a raw dump |
| `lb_logs_query` | `lb:read` | LogQL against that LB's Loki stream **only**, tenant label injected server-side |
| `cert_list` / `cert_upload` / `cert_order` | `lb:cert:write` | **Never returns a private key** — not to an agent, not to anyone |
| `lb_explain` | `lb:read` | Compiles the current spec into a human-readable "a request to `host+path` goes to *this* target group, because rule *N*" trace. Purpose-built for LLM debugging. |

### 7.2 Rules the MCP surface adds on top of the API

- **Same token, same authorization.** The MCP server is a client of `fuzelb-api`; it
  holds no privilege of its own and forwards the caller's FuzeFront token. There is no
  MCP-specific authz path to get wrong. An agent can never exceed its principal.
- **Read/write split is explicit.** A session started with a `lb:read` token cannot be
  talked into a mutation by any prompt, because the capability is not in the token.
  This is the reason scope lives in the token rather than in the MCP server's config.
- **Mutations are confirm-gated and always echo the diff** the mutation will cause
  before applying it.
- **Responses are bounded** (paginated, truncated, downsampled). An unbounded log or
  metric dump is both a context-window hazard and a data-exfiltration one.
- **Tool output is data, not instructions.** Access-log lines and header values are
  attacker-controlled; the server marks them as untrusted content in tool results.

---

## 8. Implementation plan

Phased, each phase independently mergeable, each with a verifiable acceptance
criterion. Nothing in a phase is "done" on a green CI alone — the repo's
`verification-protocol` applies (confirm remote SHA, confirm the PR, exercise the
thing).

| M | Deliverable | Acceptance criterion |
| --- | --- | --- |
| **M0** | *This document*, reviewed and approved. Decisions in §12 resolved. | Design PR merged. No runtime change. |
| **M1** | **Frozen contract**: `services/fuzelb-api/openapi.yaml` + generated `@fuzefront/fuzelb-client` + mock server. | `oasdiff` clean; client builds; mock serves every path; contract test in place. Unblocks M2–M6 in parallel. |
| **M2** | **Control-plane API**: FastAPI `fuzelb-api`, Postgres schema + migrations, authz middleware (§6.4), audit table. Stub reconciler. | `tests/test_fuzelb_api.py` offline; `tests/test_fuzelb_authz.py` proves cross-tenant read → `404`, missing scope → `403`, bad `aud` → `401`; generated schema matches the frozen spec. |
| **M3** | **Config compiler**: spec → nginx bundle, `nginx -t` gate, generation/rollback, SSRF + link-local guards. | Golden-file tests over a rule matrix; a malicious spec (metadata IP, traversal, CRLF in a header value) is rejected, with a test per attack. |
| **M4** | **Data-plane node image + agent + prober**: cloud-init template (mirroring `elastic-userdata-privnet.template`), bundle pull over mTLS, atomic reload, prober, exporters. | Two VPS in a lab tenant serve a real backend; kill node A → traffic continues on B; `nginx -t` failure never goes live. |
| **M5** | **Reconciler + IPAM + VIP failover**: Contabo client reuse, pool allocation/quarantine, keepalived per-LB, DNS publication. | `POST /load-balancers` → `active` end to end. **Hard-kill node A and measure actual failover**, VIP reassignment confirmed via the Contabo API, as the `api-floating-vip` work did (run 35718868309). Record the measured number; do not claim one. |
| **M6** | **FuzeFront user space**: `/lb/*` public API app, token minting (`aud=fuzeinfra-lb`), JWKS + cache, tenant/quota/entitlement, console UI, CF Access apps. | A real customer session creates an LB through the browser; a `lb:read` token is proven unable to mutate. |
| **M7** | **MCP server** (§7) + `lb_explain`. | An agent diagnoses a seeded 502 (one unhealthy target) from `target_health` + `lb_explain`, unassisted. |
| **M8** | **Observability + metering**: dashboards, alert rules, metering pipeline → billing events. | Grafana dashboard live per LB; a synthetic month of traffic produces a metering total reconciling with the raw counters within 1%. |
| **M9** | **Hardening + GA**: soak, chaos (kill a node, expire a cert, dry the IP pool, partition the VLAN), runbook, SLA, docs. | `docs/consuming-repos/LOAD_BALANCER.md` + `docs/runbooks/fuzelb-*.md`; every chaos scenario has a documented, *observed* outcome. |

**Parallelism.** M1 is the gate; M2/M3/M4 and the M6 UI can then run as concurrent
slices against the frozen contract — the repo's contract-first fan-out.

**GitOps throughout.** Every chart/values change lands in Git and syncs via Argo. No
`kubectl patch` against prod — selfHeal reverts it within seconds, and it has already
cost this repo a debugging cycle. Each service is behind an `enabled` gate defaulting
to `false`, wired into all three values overlays, and its SealedSecret lands in the
same change that enables it.

---

## 9. Commercial shape (for the Contabo-customer offer)

| Size | Data plane | Positioning |
| --- | --- | --- |
| `small` | 2 × VPS S | Dev/staging, low thousands rps |
| `medium` | 2 × VPS M | Production web/API |
| `large` | 3 × VPS L | High-throughput, extra failure domain |

Pricing inputs, so the margin is computed rather than guessed: 2–3 VPS + 2–3
additional IPs + the private-networking add-on per node + control-plane amortization +
egress + **the cancellation-lag cost from §4.3** (Contabo cancellation is
end-of-billing-period, so a short-lived LB's instances are still paid to period end;
under monthly churn this is the difference between a healthy and a negative margin,
and it argues for either a minimum term or instance reuse across tenants after a
wipe — an open decision, §12).

Metering: per-LB-hour, per-GB processed, per-million-requests, from the same
Prometheus counters the customer sees — so an invoice dispute is resolvable against a
dashboard the customer already has. Metering events flow to FuzeFront's billing
asynchronously (§6.5); a metering outage never blocks traffic.

---

## 10. Security and SLA posture

- **Tenant isolation:** separate instances, IPs, nginx processes, DB rows, Grafana
  scope. No shared proxy process between tenants in v1.
- **Secrets:** TLS private keys sealed at rest, delivered to nodes over mTLS, mode
  `0600`, never returned by any API or MCP tool, never logged. Contabo API credentials
  live only in the reconciler, never on a data-plane node — a compromised LB node must
  not be able to move VIPs. (keepalived's VIP reassignment credential is the exception
  and is therefore scoped and audited; if Contabo ever offers per-resource API scoping,
  use it here first.)
- **Bootstrap tokens** in cloud-init are one-time and expire in 15 minutes.
  `userData` is plaintext on the Contabo side — so nothing long-lived goes in it.
- **Egress from LB nodes** is restricted to the reconciler, the customer's declared
  targets, ACME, and telemetry. A proxy that can reach anything is an SSRF pivot.
- **SLA v1:** 99.9% per LB, measured as "at least one node serving." Explicitly
  **node-level**, not region-level (§4.4). Publish the measurement method, not just
  the number.
- **Compliance:** access logs may contain PII (IPs, URLs). Per-LB retention is
  customer-configurable, deletion honours the LB's deletion, and the DPA says so.

---

## 11. Testing and verification

- **Offline unit tests** in `tests/` (repo convention, no network): contract match,
  authz matrix, config-compiler golden files, malicious-spec rejection, IPAM
  allocation/quarantine, Contabo client behaviours (UUID header, plain userData,
  plural `instances`, `{}` body) against a recorded fake.
- **kind e2e**: control plane + a stub provider + containerized data-plane nodes;
  exercises create → route → deregister → delete without touching Contabo.
- **Lab tenant on real Contabo**: the only place failover timing is *measured*.
- **Chaos**: kill a node; expire a certificate; dry the IP pool; partition the VLAN;
  feed a bundle that fails `nginx -t`; roll back a generation. Each has an expected
  and an **observed** outcome recorded in the runbook.
- **Load**: establish the per-size-class rps and connection ceilings and publish them.
  An unpublished ceiling is discovered by a customer, in production.

---

## 12. Open decisions (need a human call before M1)

1. **IP pool depth and refill process.** Who buys, on what trigger, with what lead
   time? §4.1 is the most likely public capacity failure and it has no owner yet.
2. **Cancellation-lag cost** (§4.3, §9): minimum term, or instance reuse after a
   verified wipe? Reuse is cheaper and is a cross-tenant risk that needs a written
   wipe procedure.
3. **Go-to-market:** direct-to-Contabo-customers under our brand, or a partnership /
   white-label with Contabo? This changes the identity model at the edge (our
   FuzeFront accounts vs. Contabo SSO) and should be settled before M6.
4. **Region list for v1** — EU only, or EU + US? Each region needs its own IP pool.
5. **`large` = 3 nodes or 2 bigger nodes?** Three nodes buys a failure domain; two
   buys throughput per euro.
6. **WAF in v1.5 or v2** — ModSecurity + CRS is real support load (false positives are
   the top LB-support ticket category industry-wide).
7. **Does FuzeFront want `/lb/*` under `api.fuzefront.com`, or a dedicated
   `lb.fuzefront.com` app?** Affects CF Access app layout and the console's routing.

---

## 13. What this PR does and does not do

**Does:** add this design document and an illustrative OpenAPI sketch under
`docs/design/`.

**Does not:** change any chart, values file, Terraform resource, workflow, or running
service. No `enabled` gate is flipped, no secret is added, no Contabo resource is
created, no prod state is touched. Implementation begins only after M0 approval, and
lands GitOps-only, phase by phase, per §8.
