# Node Provisioning Runbook

Mandatory steps when adding a new k3s node to the FuzeInfra cluster. Every step is
required — skip any one and runners or workloads on the new node will malfunction in
ways that look unrelated to the join itself.

## Post-join checklist

### 1. Update UFW on the control plane

The control plane's UFW must explicitly allow traffic from the new node's IP on the ports
k3s and flannel VXLAN use. Without this, VXLAN is one-directional and pods on the new
node cannot resolve external DNS or reach the API server through the flannel overlay.

```bash
# Run on the CONTROL PLANE (not the new node)
NEW_NODE_IP=<new-node-ip>
NODE_NAME=<node-name>    # e.g. fuzeinfra-ci-runner-2

ufw allow from $NEW_NODE_IP to any port 8472 proto udp \
  comment "flannel vxlan from $NODE_NAME"

ufw allow from $NEW_NODE_IP to any port 6443 proto tcp \
  comment "k3s api from $NODE_NAME"

ufw status numbered | grep $NEW_NODE_IP   # confirm both rules appear
```

### 2. Apply labels and taints for ARC scheduling

Runner pods use a `nodeSelector` and toleration keyed on `fuzeinfra.io/pool=ci` and the
matching taint. Without these, the ARC controller cannot schedule runner pods onto the
node even if it appears `Ready`.

```bash
NODE_NAME=<node-name>

kubectl label node $NODE_NAME fuzeinfra.io/pool=ci
kubectl taint node $NODE_NAME fuzeinfra.io/ci=true:NoSchedule
```

Verify:
```bash
kubectl get node $NODE_NAME --show-labels
kubectl describe node $NODE_NAME | grep Taints
```

### 3. Verify runner pods schedule and come online

After the UFW rules and labels/taints are in place, trigger a job on the new scale set
and confirm the runner pod lands on the new node and the runner appears online on GitHub.

```bash
# Runner pod should be on the new node
kubectl get pods -n arc-runners -o wide | grep <slug>

# Runner should show 'online'
gh api repos/<org>/<repo>/actions/runners \
  --jq '.runners[] | "\(.name) \(.status) \(.labels[].name)"'
```

---

## Troubleshooting

### GitHub Actions quota exhaustion looks identical to a broken runner

**Symptom:** Jobs fail in 2-4 seconds with an empty `runner_name` and `steps: []` in the
workflow run detail. Logs show no output from the runner itself.

**Root cause:** GitHub Actions free-tier minute quota may be exhausted. This produces the
same symptom as a completely broken runner — the job is accepted by the API but never
dispatched.

**Check this first** before debugging infrastructure:
```bash
gh api orgs/<org>/settings/billing/actions \
  --jq '{used: .total_minutes_used, limit: .included_minutes, paid: .total_paid_minutes_used}'
```

If `used >= limit` and `paid == 0`, the quota is the problem — not the runner.

---

### VXLAN return path diagnosis

Use this checklist when runner pods are `Running` but appear offline on GitHub, or when
DNS resolution fails inside pods on the new node.

1. **Check VXLAN RX on the affected node:**
   ```bash
   ip -s link show flannel.1
   ```
   If `RX packets: 0`, the return path from the control plane is broken.

2. **Check whether VXLAN packets arrive at the control plane:**
   ```bash
   # Run on the CONTROL PLANE
   tcpdump -i eth0 udp port 8472 -c 20
   ```
   If packets arrive but are dropped, UFW is the cause.

3. **Check UFW on the control plane:**
   ```bash
   # Run on the CONTROL PLANE
   ufw status | grep 8472
   ```
   The new node's IP must appear. If it does not, add the rule.

4. **Fix:**
   ```bash
   # Run on the CONTROL PLANE
   ufw allow from <node-ip> to any port 8472 proto udp \
     comment "flannel vxlan from <node-name>"
   ```

After adding the rule, test connectivity immediately — no restart required:
```bash
# From the new node
curl -s https://api.github.com/zen
```
