# Runbook — HA for the external k3s API endpoint (floating VIP + tunnel break-glass)

## Why this exists

The prod cluster runs 3 etcd members across `fuze-core-1/2/3`, so the **datastore**
is HA. The **external API endpoint is not**. `terraform/contabo/provisioning.tf`
opens `6443` on the public interface of only the **primary** control plane
(`161.97.118.134` / `vmi3383846` / `fuze-core-1`); the other two have it firewalled.
A healthy quorum therefore still has a single external endpoint — if that node dies,
external `kubectl`, GitHub Actions CD and every consumer's `cluster-query.yml` lose
the cluster even though the control plane is fine. (Observed live 2026-09-07 as a
503 → timeout → INTERNAL_ERROR progression.)

Intra-cluster API access is already HA — the `kubernetes` Service advertises the
private VLAN IPs (`control-planes.tf`, `advertise-address`). **Only the external
endpoint is the SPOF**, so a private-VLAN-only VIP would fix nothing that is not
already fixed. The design here fixes the *external* path.

## Design (hybrid — decided 2026-09-20)

Two independent external paths:

1. **Primary — Contabo floating VIP + keepalived** (drop-in, no client change).
   A Contabo **additional IP** is a floating public VIP. `keepalived`
   (`helm/fuzeinfra/templates/api-vip-keepalived.yaml`) runs unicast on the 3
   durable control planes; VRRP elects a MASTER **over the private VLAN**
   (10.0.0.0/22 — Contabo public IPs are not a shared L2 segment, so multicast
   VRRP would never converge). On promotion, `notify_master` calls the **Contabo
   API to reassign the additional IP** to that node's instance, and keepalived
   binds it locally on the public NIC (`virtual_ipaddress`). External clients keep
   a normal kubeconfig pointed at `VIP:6443` — **nothing changes on the client
   side**, which is the whole point (CD, workstation, and consumer
   `cluster-query.yml` are untouched).

2. **Break-glass — Cloudflare Tunnel TCP route** (survives a Contabo IP/network
   fault). A `tcp://kubernetes.default.svc:443` tunnel ingress rule
   (`terraform/contabo/cloudflare.tf`) exposes the in-cluster apiserver ClusterIP
   — itself HA across all 3 apiservers via kube-proxy — at
   `k8s-api.prod.fuzefront.com`, gated by a Cloudflare Access **service token**
   (`non_identity` policy). Reached with client-side `cloudflared access tcp`; this
   is a Zero Trust flow on the **free tier**, **not Spectrum** (Spectrum is only
   needed for a raw public TCP listener with no client-side cloudflared).

### Why not the other options

- **Contabo "Additional IP" was the right primitive.** Additional IPs are
  purchasable and reassignable between instances — that is what makes option 1
  work. (An earlier draft wrongly dismissed this; the reassignment is real, the
  read side is probed by `.github/workflows/contabo-check-failover-ip.yml`.)
- **Round-robin DNS** across the 3 public IPs was rejected: no health check, so
  clients keep hitting a dead node until TTL — a real improvement over one
  endpoint, **not real HA**.
- **A dedicated HAProxy node** adds a new SPOF unless itself made redundant.
- **A private-VLAN-only keepalived VIP** does not solve the *external* SPOF (see
  above), so VRRP alone is paired with the tunnel path, never shipped alone.

## The priority invariant (load-bearing)

`scripts/label-durable-nodes.sh` designates the **monitoring node**
(`fuzeinfra.io/role=monitoring`, currently `vmi3396106` / `fuze-core-2`) the
**lowest-priority** VIP holder, so the node doing bulk Prometheus/Loki disk I/O is
the **last** to also serve the API. On 2026-09-06 the monitoring stack sharing a
disk with the API-serving etcd starved it off the disk and refused connections
cluster-wide. The `apiVip.nodes` priority table in `values-contabo.yaml` MUST keep
that node last:

| node | role | priority |
|------|------|----------|
| `vmi3383846` (fuze-core-1) | primary | 200 |
| `mendys-worker-1` (fuze-core-3) | — | 150 |
| `vmi3396106` (fuze-core-2) | **monitoring** | **100 (last)** |

If the monitoring node moves, update both `label-durable-nodes.sh`'s resolution and
this table together.

## Preconditions before enabling (`apiVip.enabled: true`)

Everything ships **gated off**. Do **not** flip the gate until all of these hold —
each is a real purchase, secret, or host change that a GitOps CI apply cannot and
should not do on its own:

1. **Order the Contabo additional IP — PANEL ONLY (verified), a user action.**
   Set `apiVip.address` (+ `terraform api_vip_address`) to the assigned value once
   ordered. This CANNOT be done via Terraform/API for the existing control planes,
   confirmed against the api.contabo.com spec + a live call (probe run 35688885132,
   `GET /v1/vips` → `[]`):
   - the `additionalIps` add-on exists **only on Create Instance**, not on
     `POST /v1/compute/instances/{id}/upgrade` (which supports only
     `privateNetworking`/`backup`), so it cannot be added to a running node;
   - the VIP API (`/v1/vips`) has **no create/order** operation, only assign/unassign;
   - the only create-time path would mean rebuilding a control plane, which is
     forbidden here (`scripts/preflight_node_teardown.py`, "never wipe a node").
   So order it in the **Contabo Customer Control Panel → Add-on Manager**
   (~€3.50/mo, 1 per VPS) on a fuze-core node. There is no GitOps lever for this.
2. **Confirm the additional-IP REASSIGNMENT write path.** The endpoint is the
   Contabo **VIP API** (`/v1/vips`, verified against the api.contabo.com VIP tag) —
   NOT `secondary-ips`, which does not exist:
   - read: `GET /v1/vips/{ip}` → `.data[0].assignments[].resourceId`/`.resourceType`
   - assign: `POST /v1/vips/{ip}/{resourceType}/{instanceId}` (no body)
   - unassign: `DELETE /v1/vips/{ip}/{resourceType}/{instanceId}` (no body)

   Two things stay unverified and MUST be confirmed on real hardware before
   enabling (`contabo-check-failover-ip.yml` now does both — see its inputs):
   - the `resourceType` literal (`instances` expected) — read it back from
     `GET /v1/vips/{ip}` after one panel assignment, set `apiVip.contabo.resourceType`;
   - that a failover reassignment actually moves the IP provider-side (run the
     probe's write round-trip: assign to a test instance, then back). `notify.sh`
     already does read→unassign(old)→assign(self), so it does not depend on the
     unverified "single POST re-homes an assigned IP" semantic. Until the
     round-trip is proven green, keep the feature off — failover would bind the VIP
     locally but the provider might not route it.
3. **Instance ids — DONE.** All three `apiVip.nodes[].instanceId` are filled and
   confirmed from `GET /v1/compute/instances`: fuze-core-1 (vmi3383846) 203383846,
   fuze-core-3 (mendys-worker-1) 203410214, fuze-core-2 (vmi3396106) 203396106.
   Re-confirm the k8s node NAMES against `kubectl get nodes` at enable time (the
   values key on k8s node name, which `control-planes.tf` still records as vmi*/
   mendys-worker-1 — the fuze-core-N names are Contabo display names).
4. **Seal `contabo-api-credentials`** into the `fuzeinfra` namespace — see
   `deploy/sealed-secrets/contabo-api-credentials.yaml.template`.
5. **Open `6443` on all 3 durable CPs' public interface — codified, operator-applied.**
   Only the primary has it today. `terraform/contabo/api-vip-firewall.tf` adds the
   `ufw allow 6443/tcp` rule to all three, but it is double-gated on
   `api_vip_enabled` AND `manage_control_plane_config`, and runs over SSH from a
   workstation (CD holds no private key and must not open a public apiserver port).
   It only adds a ufw rule — no k3s restart — so it is non-disruptive on a live CP.
   Apply it in the same supervised run that flips the VIP gate. Never wipe a node
   to do this (`scripts/preflight_node_teardown.py`).

## Enable sequence (GitOps — never hand-apply to prod)

1. Land preconditions 1–5 (the SealedSecret and the values are separate commits or
   one; the secret must exist before the DaemonSet references it, or the pod stays
   `CreateContainerConfigError`).
2. In `values-contabo.yaml` set `apiVip.enabled: true` (address + reassignPath +
   instanceIds already filled), commit to `main`, let Argo sync the DaemonSet.
3. Apply the Terraform half (VIP DNS + tls-san): set `api_vip_enabled=true` /
   `api_vip_address`, and (for tls-san) run the gated
   `manage_control_plane_config` workstation apply (`control-planes.tf`) — it
   rewrites `config.yaml` and restarts k3s **one CP at a time**; verify between
   nodes.
4. Enable break-glass: `api_breakglass_enabled=true` (a PR under `terraform/**`
   triggers the apply). Provision the service-token secret to CD/kubeconfigs via
   `terraform output -raw`.

## Demonstrate failover (DoD — do NOT skip, do NOT assert)

An HA design nobody has failed over is a claim, not a result. Run this from an
operator machine with cluster + Contabo access **after** enabling. It is
destructive to one node's role, never to data, and never wipes a node.

```bash
# 0. Baseline: which node holds the VIP, and API is reachable via it.
kubectl --kubeconfig vip.kubeconfig get --raw /livez        # expect: ok
for n in vmi3383846 mendys-worker-1 vmi3396106; do
  kubectl -n kube-system logs ds/fuzeinfra-api-vip-keepalived --prefix \
    --field-selector spec.nodeName=$n | tail -3
done
# Identify the MASTER (its keepalived logs show "Entering MASTER STATE").

# 1. Isolate the current MASTER's apiserver (health-check failure path — preferred,
#    non-destructive to the host). On the MASTER node:
sudo systemctl stop k3s        # or: sudo iptables -I INPUT -p tcp --dport 6443 -j DROP

# 2. Within ~3-10s a BACKUP promotes: its keepalived logs show MASTER STATE, its
#    notify_master reassigns the Contabo IP, and virtual_ipaddress binds locally.
#    From an EXTERNAL client, the SAME kubeconfig recovers:
time kubectl --kubeconfig vip.kubeconfig get nodes    # expect: recovers, no edit

# 3. Confirm the VIP moved provider-side:
curl -sS -H "Authorization: Bearer $TOK" -H "x-request-id: $(uuidgen)" \
  "https://api.contabo.com/v1/compute/instances/<new-master-id>" | jq '.data[0].ipConfig'

# 4. Restore the original node; with nopreempt it stays BACKUP (no flap).
sudo systemctl start k3s       # or delete the iptables DROP rule
```

Paste the step-2/step-3 output into the PR/report. **The monitoring node
(`vmi3396106`) must be the last to ever hold the VIP** — verify it only becomes
MASTER when both higher-priority nodes are down.

Break-glass demonstration (independent of the VIP):

```bash
cloudflared access tcp --hostname k8s-api.prod.fuzefront.com --url 127.0.0.1:6443 \
  --service-token-id "$CF_ACCESS_CLIENT_ID" --service-token-secret "$CF_ACCESS_CLIENT_SECRET" &
kubectl --server https://127.0.0.1:6443 --token "$SA_TOKEN" get --raw /livez   # expect: ok
```

## Client kubeconfig (primary path)

No change is required for existing consumers — repoint `KUBE_CONFIG`'s `server:` to
`https://<VIP>:6443` (or `https://api.prod.fuzefront.com:6443`) when convenient; the
VIP and hostname are both in every apiserver's tls-san, so either validates.

## Rollback

- Set `apiVip.enabled: false` in `values-contabo.yaml`, commit → Argo removes the
  DaemonSet. keepalived releasing the VIP is graceful; clients fall back to the
  primary node's public 6443 (still open) or the break-glass tunnel.
- `api_vip_enabled=false` removes the DNS record; the tls-san entries drop on the
  next gated `manage_control_plane_config` apply.
- `api_breakglass_enabled=false` removes the tunnel route + Access app + token.

## From-scratch reproducibility

- The VIP DNS + tls-san are in Terraform (`api-floating-vip.tf`, `control-planes.tf`).
- The keepalived DaemonSet is in the umbrella chart, synced by Argo.
- The public-6443 firewall rule must be part of CP userdata for rebuilt nodes.
- The additional-IP order + `contabo-api-credentials` SealedSecret are the two
  manual inputs a fresh cluster needs (a purchase and a secret) — everything else
  reconciles from Git.
