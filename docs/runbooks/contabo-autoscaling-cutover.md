# Contabo Cluster Autoscaler — Prod Cutover Runbook (Task 18)

This is the ordered, gated cutover procedure for turning on the Contabo
`externalgrpc` Cluster Autoscaler provider against the **prod** Contabo k3s
cluster. Prod is GitOps (Argo CD, `selfHeal: true`) — every step that changes
cluster state happens by committing to `main` and letting Argo sync, never by
`kubectl apply`/`kubectl patch` against the live cluster.

Do not start this runbook until:

- The cutover-prep PR (name-based providerID, CA liveness probe, WireGuard
  firewall rule, canonical `deploy/elastic-userdata.template`) is merged to `main`.
- You have a maintenance window — the spike test at the end intentionally
  provisions and destroys a real Contabo VPS.

## 0. Preconditions

- `helm/fuzeinfra/values-contabo.yaml` still has `clusterAutoscaler.enabled: false`
  and `overprovisioning.enabled: false` going into this runbook — they only flip
  in step 6, together, in one PR.
- You have Contabo API credentials (client ID/secret, API user/password) with
  permission to create/delete/tag VPS instances, and an existing Contabo SSH
  key ID to inject into new nodes.

## 1. WireGuard server reprovision caveat

`modules/contabo-k3s-node/cloud-init.tftpl` and the new
`deploy/elastic-userdata.template` both open `51820/udp` (WireGuard) alongside
the existing `8472/udp` (VXLAN) so agent nodes work under either flannel
backend during the transition.

- The **k3s server** must be reprovisioned (or reconfigured) onto
  `--flannel-backend=wireguard-native` before elastic agents are expected to
  actually route pod traffic over WireGuard. Until that happens, agents joining
  with only the new firewall rule will still fall back to VXLAN (8472/udp) —
  which is fine and expected during the transition, but don't assume WireGuard
  is "live" just because the port is open.
- This is a **server-side change with a network blip**: flannel backend
  changes typically require restarting k3s server and re-establishing the
  overlay. Plan this as its own change window, ideally BEFORE the elastic pool
  is enabled, so a flaky backend switch doesn't get confused with an
  autoscaler bug.
- Verify after the switch: `kubectl -n kube-system get pods -l app=flannel`
  (or equivalent) healthy, and existing pod-to-pod traffic across nodes still
  works, before proceeding.

## 2. Bring up the 3-node baseline + set the k3s node token

- Confirm the baseline (non-elastic) 3-node k3s cluster is up via the existing
  `terraform/contabo` module and `modules/contabo-k3s-node`.
- Retrieve the k3s node join token from the server:
  `sudo cat /var/lib/rancher/k3s/server/node-token` (or wherever your
  `k3s_node_token` Terraform variable sources it from).
- This token becomes `K3S_NODE_TOKEN` in step 3 — treat it as a secret; it is
  never committed to git.

## 3. Create the `fuzeinfra-ca-provider` SealedSecret

The provider Deployment (`helm/fuzeinfra/templates/autoscaler/provider-deployment.yaml`)
loads `envFrom.secretRef` on `clusterAutoscaler.provider.existingSecret`
(defaults to `fuzeinfra-ca-provider`). Create a SealedSecret with **exactly**
these keys (see `cluster-autoscaler/contabo-externalgrpc/cmd/server/main.go`
for the authoritative env var list):

| Key | Source |
|---|---|
| `CONTABO_CLIENT_ID` | Contabo OAuth2 client ID |
| `CONTABO_CLIENT_SECRET` | Contabo OAuth2 client secret |
| `CONTABO_API_USER` | Contabo API username |
| `CONTABO_API_PASSWORD` | Contabo API password |
| `K3S_NODE_TOKEN` | the token retrieved in step 2 |
| `SSH_KEY_ID` | the Contabo SSH key ID to inject into new elastic nodes |

```bash
kubectl -n fuzeinfra create secret generic fuzeinfra-ca-provider \
  --dry-run=client -o yaml \
  --from-literal=CONTABO_CLIENT_ID='...' \
  --from-literal=CONTABO_CLIENT_SECRET='...' \
  --from-literal=CONTABO_API_USER='...' \
  --from-literal=CONTABO_API_PASSWORD='...' \
  --from-literal=K3S_NODE_TOKEN='...' \
  --from-literal=SSH_KEY_ID='...' \
  | kubeseal --format yaml \
  > helm/fuzeinfra/templates/autoscaler/sealed-secret-ca-provider.yaml
```

Commit the generated SealedSecret (never the plaintext) to `main` so Argo
applies it. Confirm it unseals:
`kubectl -n fuzeinfra get secret fuzeinfra-ca-provider` shows up after sync.

Note `K3S_SERVER_URL` is NOT in the secret — it's a plain (non-secret) value
set directly in `values-contabo.yaml` (`clusterAutoscaler.provider.k3sServerUrl`).

## 4. Fill productId / imageId / k3sServerUrl — verify against the Contabo catalog

In `helm/fuzeinfra/values-contabo.yaml` under `clusterAutoscaler.provider`:

- `productId`: the Contabo VPS/VDS SKU for elastic nodes (e.g. `V45`).
  **Cross-check against `productSpecs` in
  `cluster-autoscaler/contabo-externalgrpc/internal/provider/template.go`** —
  only `V1, V45, V46, V47, V76, V92` are whitelisted, and every entry except
  `V45` is marked "best-effort — verify against catalog" in that file. Confirm
  the actual vCPU/memory for your chosen SKU against Contabo's current
  published catalog (`GET /v1/pricing` or the contabo.com VPS page) before
  relying on it for scheduling-critical scale-from-zero decisions — a wrong
  spec here makes `NodeGroupTemplateNodeInfo` lie to Cluster Autoscaler about
  node capacity.
- `imageId`: the OS image ID (`GET /v1/compute/instances/images`) matching
  what the baseline nodes run (same k3s/OS compatibility).
- `k3sServerUrl`: `https://<contabo-server-ip>:6443`.
- **Verify the Contabo tag-assignment body shape**: `internal/contabo/client.go`'s
  `Create` calls `POST /v1/compute/instances/{id}/tag-assignments` with
  `{"tags": ["<name>", ...]}` (tag *names*, not numeric tag IDs) after the
  instance is created. Confirm this still matches Contabo's current API before
  the first real create — if Contabo expects tag IDs instead of names, elastic
  instances will be created untagged and `ListByTag` will never find them
  (silently breaking scale-down and target-size reporting).

## 5. Base64 the elastic-userdata.template

```bash
base64 -w0 cluster-autoscaler/contabo-externalgrpc/deploy/elastic-userdata.template
```

Paste the output into `clusterAutoscaler.provider.userDataTemplateB64` in
`values-contabo.yaml`. Sanity-check by decoding it back and diffing against
the source file before committing:

```bash
echo '<pasted-b64>' | base64 -d | diff - cluster-autoscaler/contabo-externalgrpc/deploy/elastic-userdata.template
```

Confirm the template's `--kubelet-arg 'provider-id=contabo://{{.NodeName}}'`
line is intact — this is what makes scale-down correlation work (see that
file's header comment and
`cluster-autoscaler/contabo-externalgrpc/internal/provider/size.go`).

## 6. Flip enabled flags + Argo sync

In one PR to `main`, in `helm/fuzeinfra/values-contabo.yaml`:

```yaml
clusterAutoscaler:
  enabled: true
overprovisioning:
  enabled: true
```

Merge, then confirm Argo CD (`fuzeinfra-prod` Application) picks up the
change and syncs cleanly:

```bash
argocd app get fuzeinfra-prod
kubectl -n fuzeinfra get pods -l app.kubernetes.io/name=fuzeinfra-cluster-autoscaler
kubectl -n fuzeinfra get pods -l app.kubernetes.io/name=fuzeinfra-contabo-ca-provider
```

Both the CA pod and the provider pod should reach `Running`/`Ready`. Check CA
logs for a clean `NodeGroups` / `Refresh` loop with no gRPC errors against the
provider, and check the provider pod's liveness (`livenessProbe.tcpSocket`
on the grpc port) and the CA's own `/health-check` on `:8085` are green
(`kubectl -n fuzeinfra get pods` shows no restarts from either).

## 7. Gated spike test

With both pools enabled and floor at the baseline (3 nodes, elastic min=0):

1. Apply load that forces at least one pod to go unschedulable against the
   baseline + overprovisioning ceiling (e.g. scale a test Deployment with
   resource requests sized to exceed remaining headroom).
2. **Watch elastic node provision**: `kubectl -n fuzeinfra logs -f deploy/fuzeinfra-cluster-autoscaler`
   should show a `NodeGroupIncreaseSize` call; confirm a new Contabo VPS
   appears (`ListByTag` / Contabo dashboard) named `fuzeinfra-elastic-N`.
3. **Watch it join**: `kubectl get nodes -w` — the new node should appear,
   labeled `fuzeinfra.io/pool=elastic`, tainted
   `fuzeinfra.io/elastic=true:PreferNoSchedule`, and its `Node.Spec.ProviderID`
   should read `contabo://fuzeinfra-elastic-N` (confirms the name-based
   correlation from Change 1 is working end-to-end).
4. Confirm the pending pod schedules onto it (tolerating the PreferNoSchedule
   taint, or scheduling there because nothing else fits).
5. Remove the load and **watch drain + delete**: after
   `scale-down-unneeded-time` (default 45m) elapses with the node unneeded, CA
   should cordon/drain it and call `NodeGroupDeleteNodes`; confirm the Contabo
   VPS is actually deleted (not just the k8s Node object) via the Contabo
   dashboard or `ListByTag`.
6. **Confirm TF plan stays clean**: `terraform plan` in `terraform/contabo`
   should show no drift — the elastic node lifecycle is entirely
   provider/CA-managed and must never appear in Terraform's state.
7. **Confirm the floor returns to 3**: once the elastic node is deleted,
   `kubectl get nodes` should show exactly the 3 baseline nodes again, and
   `NodeGroupTargetSize` (queryable via the provider's gRPC or CA's own
   metrics) reports 0 elastic nodes.

If all of the above hold, the cutover is validated.

## Rollback

If anything in the spike test misbehaves (nodes fail to join, scale-down
deletes the wrong thing, TF drift appears, etc.):

1. Revert `helm/fuzeinfra/values-contabo.yaml` — flip
   `clusterAutoscaler.enabled` back to `false` (and `overprovisioning.enabled`
   back to `false` if it's implicated) in a PR to `main`.
2. Merge and let Argo sync the rollback the same way it synced the rollout —
   never `kubectl delete`/`kubectl scale` directly against prod.
3. If a stray elastic VPS is left over (e.g. the provider crashed mid-delete),
   manually delete it via the Contabo dashboard/API and confirm
   `terraform plan` is clean afterward.
