# Off-VLAN nodes: failure policy, provisioning-path audit, and remediation

Prod is a single private VLAN — Contabo network **60932**, CIDR **10.0.0.0/22**,
data centre "European Union 2". Every k3s node is supposed to register on an
address inside it. This document says what happens when one does not, why that
choice was made, which provisioning paths can produce a node, and how to fix the
one node that cannot fix itself.

Companion code: `cluster-autoscaler/contabo-externalgrpc/deploy/elastic-userdata.template`,
`modules/contabo-k3s-node/cloud-init.tftpl`, `helm/fuzeinfra/rules/nodes.yml`,
`tests/test_elastic_userdata_vlan.py`.

---

## 1. What an off-VLAN node actually costs

Measured on prod 2026-09-02, not inferred.

`fuzeinfra-ci-runner-2` registered `InternalIP 13.140.158.203` **and**
`2a02:c207:2354:3725::1`. `fuzeinfra-ci-runner-1`, same pool, same Terraform
module, registered `10.0.0.4`.

```
$ kubectl -n fuzeinfra logs fuzeinfra-node-configurator-p7v97 --tail=2     # pod on ci-runner-2
Error from server: Get "https://13.140.158.203:10250/containerLogs/...":
  proxy error from 127.0.0.1:6443 while dialing 13.140.158.203:10250, code 502: 502 Bad Gateway

$ kubectl -n arc-runners logs fuzebi-jzz8n-runner-lbcxk --tail=2           # pod on ci-runner-1
[WORKER 2026-09-02 06:24:10Z INFO ActionManager] Action post node.js file: ...
```

So the concrete cost is: **`kubectl logs` / `exec` / `port-forward` fail for every
pod on the node.** Port 10250 is only reachable over the VLAN, and the apiserver
proxies to whatever `InternalIP` the node registered. The node becomes
undebuggable. Any attempt to run a diagnostic pod can itself land there and come
back empty — which happened while writing this document.

**What it does NOT cost, stated honestly:** pods still schedule, get IPs, pass
probes and run. Flannel still peers. `ImagePullBackOff` and
`CreateContainerConfigError` are present on both off-VLAN *and* on-VLAN nodes in
prod right now, so those are **not** caused by VLAN membership and should not be
attributed to it.

Secondary effect: with no `--node-ip`, k3s auto-detects both address families and
registers a dual InternalIP. kube-state-metrics then reports the **IPv6** as
`kube_node_info.internal_ip`. This is a symptom of the missing `--node-ip`, not of
`/etc/gai.conf`; the `nodeConfigurator.preferIPv4` DaemonSet does not and cannot
fix it.

---

## 2. The policy: QUARANTINE

Three options were on the table when the private NIC never appears.

| Policy | Behaviour | Why not / why |
| --- | --- | --- |
| **fail-SOFT** (what elastic did) | join anyway without `--node-ip`, log a warning | The warning goes to a cloud-init log nobody reads. The node then serves as capacity while being undebuggable, and nothing in `kubectl get nodes` distinguishes it. This is how two nodes sat off-VLAN unnoticed. |
| **fail-LOUD** (what `modules/contabo-k3s-node` did) | refuse to join; exit non-zero | Correct for a human-driven `terraform apply` — abort, fix, retry, cheap. Wrong for the autoscaler: Cluster Autoscaler *retries*, each retry is a real VPS create, and **Contabo cancellation is end-of-billing-period**, so every refusing node costs a full month while CA burns `max-node-provision-time` before trying again. One misconfiguration becomes an unbounded bill, and a scale-up yields zero capacity with nothing in the cluster to look at. |
| **QUARANTINE** (chosen, both paths) | join, but with `fuzeinfra.io/vlan=absent` + `fuzeinfra.io/off-vlan=true:NoSchedule` | Takes the useful half of each. The node **registers**, so the failure is visible in `kubectl get nodes`, alertable from cluster metrics, and the billing-aware reaper can release it at the next boundary. It registers **unschedulable**, so it can never quietly serve as undebuggable capacity. |

Both the elastic path and the Terraform module now implement the same policy, so
there is one behaviour, one label, and one set of alerts.

Two supporting details:

* The healthy path is labelled **symmetrically** (`fuzeinfra.io/vlan=present`).
  "No label at all" must not read as healthy — it means the node came from a path
  that is not reporting, which is precisely the blind spot that let
  `fuzeinfra-ci-runner-2` happen. `NodeMissingVLANLabel` covers that case.
* Quarantine is **not terminal**. The join runs from a systemd oneshot that
  re-runs on every boot until it succeeds, and the private-network assign
  typically reboots the node — so a late assign repairs the node with no human
  step. See §3.

### Alerts (`helm/fuzeinfra/rules/nodes.yml`)

| Alert | Expression | Severity | Catches |
| --- | --- | --- | --- |
| `NodeOffPrivateVLAN` | `kube_node_info{internal_ip!~"10[.]0[.][0-3][.].*"}` | critical | **Any** node on **any** path, audited or not — it reads the node's real registered IP. This is the backstop. |
| `NodeQuarantinedOffVLAN` | `kube_node_labels{label_fuzeinfra_io_vlan="absent"}` | critical | A node whose cloud-init deliberately self-quarantined. |
| `NodeMissingVLANLabel` | node info `unless` a `vlan=` label, excluding control planes | info | A node from a path that does not report VLAN status at all. |

Verified against live prod Prometheus (read-only) at authoring time:
`NodeOffPrivateVLAN` returned exactly the 2 off-VLAN nodes and none of the 4 VLAN
nodes; `NodeMissingVLANLabel` returned the 3 agent nodes with control planes
correctly excluded.

---

## 2a. Standing provisioning policy: every node is born on the VLAN

**Rule: any node provisioned into this cluster is created WITH the paid Contabo
`privateNetworking` add-on, and attached to net 60932, as part of provisioning.
There is no per-node cost decision and no human gate.**

The add-on is a per-instance charge, so this was previously treated as a
judgement call to be escalated each time. It is not. An off-VLAN node is not a
cheaper node -- it is a broken one (S1: `kubectl logs`/`exec` against it fail,
kubelet port 10250 is only reachable on the VLAN). Paying per node is the cost
of the node working at all, so it belongs in the provisioning path, not in a
decision queue.

Concretely, for each way a node can be created:

| Path | How the add-on is ordered | Status |
|---|---|---|
| Elastic (autoscaler) | `createInstance` body `addOns:{privateNetworking:{}}`, gated by `clusterAutoscaler.provider.privateNetworking` | wired; `true` in `values-contabo.yaml` |
| Terraform (CI workers, control planes) | `contabo_instance` `add_ons { id, quantity }` at create, plus `private_network_id` | `private_network_id` wired in `ci-workers.tf`; see the note below |
| Existing instance | `POST /v1/compute/instances/{id}/upgrade {"privateNetworking":{}}` via `ca-private-net` `action=upgrade` | wired |

**The add-on id is `1477`.** That is the concrete value the Terraform
`add_ons { id = 1477, quantity = 1 }` block needs, and it was previously
unrecorded anywhere in this repo -- which is part of why the Terraform route
was written off as impossible. Confirmed against the live API on 2026-09-03
while ordering it for `fuzeinfra-ci-runner-2`:

```
POST /v1/compute/instances/203543725/upgrade  {"privateNetworking":{}}
  -> HTTP 200
     { "instanceId": 203543725, "addonsIds": [ 1477 ] }
```

A new provisioning path is not complete until it orders the add-on. Treat a PR
that adds one without it the same as a PR that omits the k3s join token.

**This is API-orderable, not a panel purchase.** That belief caused the HTTP 402
to be read as "blocked on a human buying something" for longer than it should
have been. All three routes above are automated.

## 3. The assign-before-boot race

`internal/contabo/client.go` `Create()` necessarily runs in this order:

```
POST /v1/compute/instances  (addOns.privateNetworking)   <- node starts booting HERE
  -> waitForInstanceVisible
  -> applyTags
  -> POST /v1/private-networks/60932/instances/{id}      <- assign; MAY REBOOT the node
```

The assign lands **while cloud-init is already running**, and it may reboot the
instance. `runcmd` is a once-per-instance module, so a reboot part-way through it
can leave the k3s join half-done and never retried.

Three changes make the node converge regardless of ordering:

1. **The join lives in a systemd oneshot**, not in `runcmd`:
   `fuzeinfra-vlan-join.service` (elastic) / `fuzeinfra-node-join.service`
   (module), each gated on `ConditionPathExists=!<sentinel>` and
   `WantedBy=multi-user.target`. It runs on **every** boot until it has succeeded
   on the VLAN. A reboot re-runs it instead of destroying it.
2. **The wait loop re-applies `netplan`** on each poll, because the vNIC can
   materialise only once the assign lands — after the first `netplan apply` ran.
3. **The sentinel is written only on a VLAN-successful join.** A quarantined node
   leaves it absent, so the next boot re-enters the script, finds the NIC, writes
   `/etc/rancher/k3s/config.yaml` with `node-ip`/`flannel-iface`/`node-external-ip`
   and restarts `k3s-agent`. **The reboot is the repair trigger, not the failure
   mode.**

One residual manual step: k3s applies `--node-label`/`--node-taint` at
**registration only**, so a repaired node keeps its quarantine marks until an
operator clears them. The script logs the exact commands:

```
kubectl label node <name> fuzeinfra.io/vlan=present --overwrite
kubectl taint node <name> fuzeinfra.io/off-vlan-
```

### The bug underneath the race

PR #830 added a 300 s wait for an address on `eth1` — but never **configured**
`eth1`. cloud-init's fallback network config brings up only the primary NIC, so
`eth1` had no DHCP client and the wait could not succeed on any node, in any
ordering. Every elastic node burned 300 s and then took the fail-open branch. A
wait without a netplan is not a fix. `modules/contabo-k3s-node/cloud-init.tftpl`
had always written the netplan drop-in; the elastic template never did.

PR #830 also edited **only the base64 blob** in `values-contabo.yaml` and left
`deploy/elastic-userdata.template` untouched, arming two silent regressions: the
next `ca-cutover` run regenerates the blob **from the template** and would have
reverted the fix, and `ca-salvage-enroll` reinstalls **from the template** and so
kept minting off-VLAN nodes. `tests/test_elastic_userdata_vlan.py` now makes that
drift a red build.

---

## 4. Provisioning-path audit

Every path that can put a node in this cluster, and whether it satisfies all
three requirements: (a) the paid per-instance `privateNetworking` add-on,
(b) membership of network 60932, (c) `--node-ip` + `--flannel-iface` on the join.

| # | Path | Creates | (a) add-on | (b) assign | (c) node-ip/flannel-iface | Before | After |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `terraform/contabo/vps.tf` + `provisioning.tf` | control planes | manual API `upgrade` | manual / imported | yes — `config.yaml` under `local.private_net_enabled` | OK | unchanged |
| 2 | `terraform/contabo/control-planes.tf` | nothing (config only) | n/a | n/a | n/a | OK | unchanged |
| 3 | **`terraform/contabo/ci-workers.tf`** → `modules/contabo-k3s-node` | CI runners | **no** | **no** | **no** | **BROKEN — produced `fuzeinfra-ci-runner-2`** | passes `private_network_id`; module cloud-init does (c); handler/`ca-private-net` does (a)+(b) |
| 4 | `infra-request-handler.yml` → consumer `deploy/terraform` → same module | consumer nodes | **no** | **no** | **no** | **BROKEN (latent)** | module defaults `private_network_id=60932`; new post-apply step does `upgrade` + `assign` |
| 5 | cluster-autoscaler `scale.go` / `client.go` + `userDataTemplateB64` | elastic nodes | yes (#830) | yes (#830) | **present but dead — no netplan, so the wait always timed out** | **BROKEN** | netplan added; join is reboot-proof; quarantine on failure |
| 6 | `ca-salvage-enroll.yml` (reinstall from `deploy/elastic-userdata.template`) | re-enrolled elastics | n/a | n/a | **no — template had zero VLAN content** | **BROKEN** | fixed by rewriting the template it reads |
| 7 | `ca-cutover.yml` (regenerates the blob from that same template) | nothing directly | n/a | n/a | **would have reverted #830** | **BROKEN** | template is now the source of truth; parity test guards it |
| 8 | `ca-private-net.yml` (`upgrade` / `assign` / `enroll-elastics`) | nothing — mutates membership | yes | yes | n/a | OK | unchanged; now the documented remediation tool |

Two structural changes exist specifically so that a path **not on this list** is
still covered:

* `modules/contabo-k3s-node`'s `private_network_id` **defaults to 60932**. A
  caller that says nothing gets a VLAN node. Silence was exactly how path 3
  failed, and a per-caller fix cannot cover callers that do not exist yet.
* `NodeOffPrivateVLAN` alerts on the node's **actual registered IP**, so it fires
  for a node from any path, including one nobody has audited.

`private_network_id` is deliberately used instead of `private_network_name`:
name-mode makes Terraform **create and reconcile** a `contabo_private_network`
whose `instance_ids` are exactly that request set, which against live net 60932
would attempt to **detach the control planes and every elastic node**. See the
ELASTIC-EXCLUSION note in `terraform/contabo/private-network.tf`.

---

## 5. Remediating `fuzeinfra-ci-runner-2`

This node **will not self-heal**. It is not autoscaler-managed, so the
billing-aware reaper will never replace it, and it booted from the old cloud-init
which has no join unit to re-run. The source path is fixed, so this cannot recur.

**It does NOT need a human to decide whether to buy the add-on** -- see the
standing policy in section 2a; that decision is already made, for every node.
Nor does it need a human to run the purchase: `ca-private-net`
`action=upgrade` orders the add-on over the API, and `action=assign` attaches
it. Option A below is a sequence of workflow dispatches, not a manual purchase.

What still warrants a person choosing a moment, rather than a person choosing
an outcome: the assign step **may reboot the node**, and it carries the ARC
runners, so it should land in a CI-quiet window. **No destructive step was
taken by this change.**

Current state (verified):

```
fuzeinfra-ci-runner-2   Ready   <none>   2d20h   v1.36.4+k3s1
  InternalIP  13.140.158.203
  InternalIP  2a02:c207:2354:3725::1
  taints      fuzeinfra.io/ci=true:NoSchedule
  providerID  k3s://fuzeinfra-ci-runner-2
```

### Option A — attach + rejoin in place (preferred; keeps the runner)

1. Find the Contabo instance id:
   `ca-private-net` → `action=list-instances`, match `fuzeinfra-ci-runner-2`.
2. Order the add-on: `ca-private-net` → `action=upgrade`, `instance_id=<id>`.
   Must be **before** the assign, or the assign returns HTTP 402.
3. Assign: `ca-private-net` → `action=assign`, `network_id=60932`,
   `instance_id=<id>`. **This may reboot the node** — do it in a CI-quiet window;
   ARC jobs on it will be interrupted.
4. Contabo requires the NIC to be surfaced. If `eth1` does not appear after the
   reboot, the instance needs a **reinstall** (see
   `docs/design/s3-and-private-networking.md`) — which is Option B.
5. Over SSH, write the netplan drop-in and rejoin k3s on the private address:
   ```
   cat >/etc/netplan/60-eth1-private.yaml <<'EOF'
   network: {version: 2, ethernets: {eth1: {dhcp4: true, optional: true}}}
   EOF
   chmod 600 /etc/netplan/60-eth1-private.yaml && netplan apply
   PRIV=$(ip -4 -o addr show eth1 | awk '{print $4}' | cut -d/ -f1)
   PUB=$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)
   mkdir -p /etc/rancher/k3s
   printf 'node-ip: %s\nflannel-iface: eth1\nnode-external-ip: %s\n' "$PRIV" "$PUB" \
     > /etc/rancher/k3s/config.yaml
   ufw allow from 10.0.0.0/22
   systemctl restart k3s-agent
   ```
6. Verify from a workstation: `kubectl get node fuzeinfra-ci-runner-2 -o wide`
   shows an InternalIP in `10.0.0.0/22`, and `kubectl logs` against a pod on it
   succeeds. `NodeOffPrivateVLAN` clears within 10 minutes.

### Option B — replace (simplest, costs a rebuild)

`fuzeinfra-ci-runner-2` was created by Terraform (`ci_workers`, index 1). With
this PR merged, a `-replace` of that instance re-creates it from the **fixed**
cloud-init, so it comes up on the VLAN by itself:

```
terraform apply -replace='module.ci_workers.contabo_instance.node["fuzeinfra-ci-runner-2"]'
```

Caveats a human must weigh, which is why this is not automated here:

* Contabo cancellation is **end-of-billing-period**, so the replaced VPS is paid
  for until then — the pool is briefly double-billed.
* The node still needs steps 2–3 above (add-on + assign) for `eth1` to exist; the
  new cloud-init then waits for it, and repairs itself on the assign reboot.
* Draining first (`kubectl drain fuzeinfra-ci-runner-2 --ignore-daemonsets`)
  avoids killing in-flight ARC jobs. Roughly 22 ARC scale sets share
  runner-1/runner-2, so CI capacity halves during the window (FuzeInfra#586).

### Also outstanding

`fuzeinfra-prod-elastic-v2-c056a22a` is likewise off-VLAN. It was created
2026-09-01T14:16Z, about **10 hours before PR #830 merged** (2026-09-02T00:06Z),
so it is evidence of the pre-#830 behaviour and **not** evidence about #830's fix.
Being autoscaler-managed it will be replaced on the next scale-down/up cycle, at
which point it picks up this cloud-init. No manual action required — but until
then `NodeOffPrivateVLAN` will fire for it, correctly.

---

## 6. What has NOT been proven

* **No real scale-up has been performed.** The autoscaler create path, the actual
  Contabo assign, and any reboot it triggers are unexercised by this change. What
  *was* exercised: the rendered cloud-config parses, the join script passes
  `sh -n`, and the script's three branches (VLAN present / absent / repair after a
  simulated post-assign reboot) were executed in a container with stubbed
  `ip`/`netplan`/`curl`/`systemctl` and produce the intended `k3s agent` argv.
* Contabo's `userData` size ceiling is not published anywhere we can cite. The
  test budget of 16 KiB is a conservative guess, not a documented limit.
* Whether restarting `k3s-agent` with a rewritten `node-ip` re-registers the
  address cleanly on a live node is **not** verified in prod. The repair path is
  written to be safe (it never re-runs the installer) but should be watched the
  first time it fires.
