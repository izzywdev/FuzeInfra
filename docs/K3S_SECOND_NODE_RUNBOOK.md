# Runbook: Add a Second Node to the k3s Cluster

FuzeInfra runs on a single-node **k3s** cluster (see the
[Contabo overlay](../helm/fuzeinfra/values-contabo.yaml)). This runbook scales it
to **two nodes** by joining a second VPS as a k3s **agent** (worker). The first
node remains the **server** (control-plane + datastore); the new node runs only
workloads.

> **Scope:** this adds an *agent* node — extra capacity for stateless workloads.
> It does **not** make the control plane highly available (that needs an
> odd number of `--server` nodes with an HA datastore). One server + one agent
> means: if the server dies, scheduling stops; existing agent pods keep running
> until they need the API.

---

## Before you start

| Need | Detail |
|------|--------|
| Server reachable | The existing k3s server's IP/hostname, reachable from the new node on **6443/TCP** |
| Node token | From the server: `sudo cat /var/lib/rancher/k3s/server/node-token` |
| Two clocks in sync | `timedatectl` / NTP on both — TLS and tokens are time-sensitive |
| Same-ish OS | A clean Ubuntu/Debian VPS is fine; ensure a unique hostname |
| Public vs private link | If the nodes talk over the **public internet**, plan to enable the **WireGuard** Flannel backend (below) |

Record the server's join info on the **server** node:

```bash
# On NODE 1 (server)
export K3S_NODE_TOKEN="$(sudo cat /var/lib/rancher/k3s/server/node-token)"
export K3S_SERVER_IP="<node1-public-or-private-ip>"
echo "$K3S_NODE_TOKEN"      # you'll paste this on node 2
```

---

## 1. Open the required node-to-node ports

k3s needs these ports reachable **between** nodes. Open them in the VPS
provider's firewall/security group **and** in the host firewall (`ufw`,
`firewalld`, nftables). Lock the source to the *other node's IP* — do not expose
these to the world.

| Port | Proto | Direction | Purpose |
|------|-------|-----------|---------|
| **6443** | TCP | agent → server | Kubernetes API server (the join target) |
| **8472** | **UDP** | both ways | Flannel **VXLAN** overlay (default backend) — pod-to-pod traffic |
| **10250** | TCP | both ways | Kubelet metrics/exec/logs (`kubectl logs`, `exec`, metrics-server) |

> If you switch Flannel to the **WireGuard** backend (Section 3), that overlay
> uses **51820/UDP** (and 51821/UDP for IPv6) **instead of** 8472/UDP — open the
> WireGuard port and you can close 8472.

Example with `ufw` (run on each node, pointing at the other node's IP):

```bash
# On NODE 2 (agent) — allow the server to reach kubelet, allow overlay
sudo ufw allow from <node1-ip> to any port 10250 proto tcp
sudo ufw allow from <node1-ip> to any port 8472  proto udp      # VXLAN default
# If using WireGuard backend instead of VXLAN:
# sudo ufw allow from <node1-ip> to any port 51820 proto udp

# On NODE 1 (server) — allow the agent to reach the API + kubelet + overlay
sudo ufw allow from <node2-ip> to any port 6443  proto tcp
sudo ufw allow from <node2-ip> to any port 10250 proto tcp
sudo ufw allow from <node2-ip> to any port 8472  proto udp
# WireGuard instead: sudo ufw allow from <node2-ip> to any port 51820 proto udp
```

Quick reachability check from node 2 before joining:

```bash
nc -vz <node1-ip> 6443        # must succeed, or the join hangs/fails
```

---

## 2. Join the second VPS as an agent

On **NODE 2**, run the k3s installer in **agent** mode, pointing at the server
URL and token. `K3S_URL` is what makes the installer set up an agent (not a new
server); `K3S_TOKEN` authenticates the join.

```bash
# On NODE 2 (agent)
curl -sfL https://get.k3s.io | \
  K3S_URL="https://<node1-ip>:6443" \
  K3S_TOKEN="<paste node-token from node 1>" \
  sh -s - agent \
    --node-name node2 \
    --node-label "fuzeinfra.io/role=worker"
```

Notes:

- `--node-name` keeps names stable/readable; otherwise the hostname is used.
- Add `--node-external-ip <node2-public-ip>` (and on the server,
  `--node-external-ip <node1-public-ip>`) when nodes communicate over public IPs
  so the overlay advertises the right addresses (pairs with WireGuard, Section 3).
- The agent service is `k3s-agent` (not `k3s`): `sudo systemctl status k3s-agent`,
  logs via `sudo journalctl -u k3s-agent -f`.

### Verify the join (from the server)

```bash
# On NODE 1 (server)
sudo kubectl get nodes -o wide
# NAME    STATUS   ROLES                  AGE   VERSION         INTERNAL-IP   ...
# node1   Ready    control-plane,master   30d   v1.x.x+k3s1     10.0.0.1
# node2   Ready    <none>                 40s   v1.x.x+k3s1     10.0.0.2

# Confirm overlay networking works: a pod on node2 can reach a Service on node1.
kubectl run net-test --image=busybox --restart=Never --overrides=\
'{"spec":{"nodeName":"node2"}}' -- sleep 3600
kubectl exec net-test -- nslookup postgres.fuzeinfra.svc.cluster.local
kubectl delete pod net-test
```

If `node2` is `Ready` but pods on it can't reach Services on `node1`, the
**8472/UDP** overlay path is blocked (or you need WireGuard) — re-check Section 1/3.

---

## 3. Flannel WireGuard backend (nodes across public IPs)

The default Flannel **VXLAN** backend sends pod traffic **unencrypted** over
8472/UDP. That's fine on a trusted private network, but when the two nodes
traverse the **public internet** (e.g. two VPSes in different DCs), encrypt the
overlay with Flannel's **WireGuard** backend.

This is set **on the server at install time** via `--flannel-backend` and
applies cluster-wide:

```bash
# On NODE 1 (server) — at install, or re-run installer to update the flag
curl -sfL https://get.k3s.io | sh -s - server \
  --flannel-backend=wireguard-native \
  --node-external-ip <node1-public-ip>
```

Then join agents with their external IP (Section 2, `--node-external-ip`).
Open **51820/UDP** between the nodes (Section 1) and you can drop 8472/UDP.

Requirements & notes:

- The WireGuard kernel module must be available (modern kernels have
  `wireguard` built in; otherwise `sudo apt install wireguard`).
- `wireguard-native` is the in-kernel backend (preferred). The older
  `wireguard` userspace backend is deprecated.
- The flannel backend is **cluster-wide and set once on the server** — you can't
  mix VXLAN and WireGuard per-node. Decide before scaling out.
- Verify the encrypted interface exists: `ip link show flannel-wg`.

---

## 4. Node-affinity: pin stateful workloads, spread stateless ones

This is the most important step. k3s's default storage is the **`local-path`**
provisioner — PVCs are backed by a directory **on whichever node the pod first
landed on**. That volume **does not follow the pod** to another node. So a
stateful pod (Postgres, Mongo, Elasticsearch, Kafka, Prometheus…) that
reschedules onto `node2` will **not see its data** on `node1`.

**Rule:** pin every `local-path`-backed / stateful workload to the node that
holds its data; let stateless workloads schedule anywhere (preferably the other
node, to actually use the new capacity).

### 4a. Pin stateful workloads to node1 (where their data lives)

Easiest: keep all FuzeInfra stateful services on `node1` with a `nodeSelector`
or `nodeAffinity`. Label the node first:

```bash
kubectl label node node1 fuzeinfra.io/storage=local-path
```

Then in the StatefulSet / Deployment spec:

```yaml
spec:
  template:
    spec:
      # Hard pin — only schedules where the data is.
      nodeSelector:
        fuzeinfra.io/storage: local-path
      # (equivalently, requiredDuringSchedulingIgnoredDuringExecution affinity)
```

Or as `nodeAffinity` (more expressive, same effect):

```yaml
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: fuzeinfra.io/storage
              operator: In
              values: ["local-path"]
```

### 4b. Schedule stateless workloads on node2 (use the new capacity)

For stateless services (your APIs, web frontends, workers — no `local-path`
PVC), *prefer* `node2` but don't hard-require it, so they still schedule if
`node2` is down:

```yaml
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: fuzeinfra.io/role        # set via --node-label at join, Section 2
              operator: In
              values: ["worker"]
```

For HA across nodes, also spread replicas with topology constraints:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels: { app.kubernetes.io/name: myproject-api }
```

### 4c. (Optional) keep general workloads off the control-plane node

If you want `node1` to focus on the stateful data services, taint it so only
tolerating (pinned) pods land there:

```bash
kubectl taint node node1 dedicated=stateful:NoSchedule
# Stateful pods then need a matching toleration:
#   tolerations: [{ key: dedicated, value: stateful, effect: NoSchedule }]
```

> **Longer term:** to let stateful pods move freely between nodes, replace
> `local-path` with networked storage (e.g. **Longhorn**) so volumes are
> replicated/attachable cluster-wide. Until then, the pinning above is required.

---

## 5. Post-join checklist

- [ ] `kubectl get nodes` shows both nodes `Ready`.
- [ ] A pod on `node2` resolves & reaches `*.fuzeinfra.svc.cluster.local` (overlay OK).
- [ ] Stateful services are pinned to `node1` and still bound to their PVCs.
- [ ] Stateless services prefer/land on `node2`.
- [ ] Firewall allows only the required ports, sourced to the other node's IP.
- [ ] (If public-IP nodes) WireGuard backend active (`ip link show flannel-wg`).

---

## Removing the agent (drain & delete)

```bash
# On NODE 1 (server) — evict workloads, then remove the node object
kubectl drain node2 --ignore-daemonsets --delete-emptydir-data
kubectl delete node node2

# On NODE 2 (agent) — uninstall k3s agent
/usr/local/bin/k3s-agent-uninstall.sh
```

---

## Related docs

- [`kubernetes-migration.md`](./kubernetes-migration.md) — the k3s/Helm stack overview.
- [`gitops.md`](./gitops.md) — Argo CD delivery model.
- [`DEPLOYING_A_SERVICE_TO_K8S.md`](./DEPLOYING_A_SERVICE_TO_K8S.md) — onboard a downstream service (resource requests/limits, affinity apply here too).
- [`../helm/fuzeinfra/values-contabo.yaml`](../helm/fuzeinfra/values-contabo.yaml) — single-node k3s overlay this scales from.
