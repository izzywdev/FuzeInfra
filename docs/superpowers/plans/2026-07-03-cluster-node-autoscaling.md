# Cluster Node Autoscaling (Contabo k3s) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically add/remove stateless Contabo k3s worker nodes in response to load, using the upstream Kubernetes Cluster Autoscaler driving a thin bespoke Contabo `externalgrpc` cloud provider.

**Architecture:** Two node groups with two owners — a static Terraform-managed **baseline pool** (3 nodes, incl. control-plane) and a Cluster-Autoscaler-managed **elastic pool** (`min=0, max=2`, ceiling 5 total). CA runs upstream and calls a small Go gRPC service that maps its `CloudProvider` interface onto the Contabo REST API, reusing the existing `modules/contabo-k3s-node` cloud-init/join recipe. Proactive headroom comes from overprovisioning pause-pods; scale-down safety (drain/PDB) comes free from CA.

**Tech Stack:** Go 1.22 (gRPC provider), Kubernetes Cluster Autoscaler `externalgrpc`, Helm, Argo CD, Terraform (Contabo provider), k3s (Flannel `wireguard-native`), Prometheus (already present).

**Spec:** `docs/superpowers/specs/2026-07-03-cluster-node-autoscaling-design.md`

## Global Constraints

- **Elastic pool bounds:** `min=0`, `max=2`. Total cluster ceiling = 5 (3 baseline + 2 elastic). Floor = 3 baseline. The provider MUST enforce `max=2` even if CA requests more.
- **Stateless-only elastic nodes:** elastic nodes carry taint `fuzeinfra.io/elastic=true:PreferNoSchedule` + label `fuzeinfra.io/pool=elastic`. Stateful services carry `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`.
- **Elastic identity tag:** every elastic Contabo instance is tagged `fuzeinfra-elastic` and named `fuzeinfra-elastic-<n>`. Terraform MUST NOT manage instances with this tag.
- **Overlay:** k3s server must run Flannel `wireguard-native` (`51820/udp`) before public-IP elastic joins.
- **GitOps only:** all deploy wiring lands via Helm (behind an `enabled` gate) + Argo CD. Never hand-deploy or `kubectl patch` prod (Argo selfHeal reverts).
- **Secrets:** reuse existing CI secrets `CONTABO_CLIENT_ID/SECRET`, `CONTABO_API_USER/PASSWORD`, `K3S_SERVER_URL`, `K3S_NODE_TOKEN`, `CONTABO_IMAGE_ID` — never duplicate/hardcode.
- **Commit trailers:** end every commit with `Co-Authored-By: Claude <model> <noreply@anthropic.com>` and `Claude-Session-Id: <session-id>`.

---

## Model Orchestration Strategy

This plan is written to be executed by a **mixed-model fleet**. Each task is tagged with a target model. The routing rule:

| Model | Gets these tasks | Why |
|---|---|---|
| **Haiku 4.5** | Small, self-contained, single-file blocks with a fully-specified interface and a concrete test already written in the plan (individual Contabo API methods, single gRPC method impls, static manifests, values-gate edits, CI workflow, annotations). | The interface + test are pre-specified here, so the block is mechanical. Cheapest model that reliably fills in a bounded, well-tested unit. |
| **Sonnet 4.6** (or Sonnet 5 when available) | Aggregation / cross-file integration / judgment tasks (assembling the gRPC server from the method blocks, the CA Helm deployment with arg tuning + RBAC, Argo + values-overlay wiring, the kind integration harness, `TemplateNodeInfo` scale-from-zero, the WireGuard overlay change). | These need whole-system context, spanning multiple files and consuming the Haiku-built blocks. Higher reasoning for wiring and subtle correctness. |
| **Opus 4.8 (orchestrator — me)** | Task sequencing, dispatching each block to the right model, two-stage review between tasks, the prod-touching prerequisites (WireGuard cutover, 3-node baseline bring-up), merge/gate decisions, and the gated staging e2e. | Owns every human-facing decision and never hands prod-risky cutovers to a cheaper model. |

**Flow per building block:** Opus dispatches a Haiku subagent with the task's Interfaces block + test → Haiku returns the unit green → Opus (or a Sonnet reviewer) gates it → once a group of blocks exists, a Sonnet subagent aggregates them into the wired component → Opus reviews and sequences the next group. The **Interfaces** block on each task is the contract that lets a Haiku worker build in isolation without seeing neighboring tasks.

Legend used below: **[MODEL: Haiku]**, **[MODEL: Sonnet]**, **[MODEL: Opus/orchestrator]**.

---

## File Structure

**New — Go gRPC provider** (`cluster-autoscaler/contabo-externalgrpc/`):
- `go.mod`, `go.sum` — module `github.com/izzywdev/fuzeinfra/contabo-externalgrpc`.
- `proto/externalgrpc.proto` + generated `*.pb.go` — vendored upstream CA externalgrpc proto.
- `internal/contabo/client.go` — thin Contabo REST client (auth, create, delete, list-by-tag).
- `internal/contabo/client_test.go` — client tests against an httptest server.
- `internal/provider/nodegroups.go` — `NodeGroups`, `NodeGroupForNode` (tag classification).
- `internal/provider/size.go` — `NodeGroupTargetSize`, `NodeGroupNodes`, `NodeGroupDecreaseTargetSize`.
- `internal/provider/scale.go` — `NodeGroupIncreaseSize` (create + cap), `NodeGroupDeleteNodes` (delete + floor).
- `internal/provider/template.go` — `NodeGroupTemplateNodeInfo` (scale-from-zero).
- `internal/provider/server.go` — server struct, config, no-op methods (`Refresh`, `Cleanup`, `GPULabel`, `PricingModel`), assembly.
- `internal/provider/*_test.go` — per-method tests with a fake Contabo client.
- `cmd/server/main.go` — flag/env config, listen, serve.
- `Dockerfile` — multi-stage build.

**New — Helm/deploy** (`helm/fuzeinfra/templates/autoscaler/`):
- `priorityclass.yaml` — negative-priority class for pause-pods.
- `overprovisioning.yaml` — pause-pod Deployment.
- `provider-deployment.yaml` — the gRPC provider Deployment + Service + Secret refs.
- `cluster-autoscaler.yaml` — CA Deployment + RBAC + ServiceAccount.
- `_autoscaler-helpers.tpl` — shared labels/helpers.

**Modified:**
- `helm/fuzeinfra/values.yaml` — add `clusterAutoscaler:` block (`enabled: false` default) + `overprovisioning:`.
- `helm/fuzeinfra/values-contabo.yaml` — enable + prod tuning.
- `helm/fuzeinfra/values-local.yaml` — keep disabled (kind uses a fake provider in tests).
- `helm/fuzeinfra/templates/databases.yaml` + stateful templates — add `safe-to-evict: "false"` annotation.
- `modules/contabo-k3s-node/cloud-init.tftpl` — ensure elastic taint/label emitted for `role=elastic`.
- `terraform/contabo/provisioning.tf` — k3s server `--flannel-backend=wireguard-native`.
- `terraform/contabo/baseline.tf` (new) — declare the 3-node baseline via the module; exclude `fuzeinfra-elastic` tag.
- `argocd/applications/fuzeinfra-prod.yaml` — no change if templates live in the umbrella chart (confirm sync).
- `.github/workflows/tf-elastic-drift-check.yml` (new) — assert no `fuzeinfra-elastic` instance appears in `terraform plan`.

---

## Task 1: Contabo REST client — auth + list-by-tag [MODEL: Haiku]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/go.mod`
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/contabo/client.go`
- Test: `cluster-autoscaler/contabo-externalgrpc/internal/contabo/client_test.go`

**Interfaces:**
- Produces:
  - `type Instance struct { ID int64; Name string; Status string; PrivateIP string; Tags []string }`
  - `type Client interface { ListByTag(ctx context.Context, tag string) ([]Instance, error); Create(ctx context.Context, req CreateReq) (Instance, error); Delete(ctx context.Context, id int64) error }`
  - `type CreateReq struct { Name string; ProductID string; ImageID string; Region string; SSHKeyID int64; UserData string; Tags []string }`
  - `func NewClient(cfg Config) *HTTPClient` where `Config{ClientID, ClientSecret, User, Pass, BaseURL string}`.

- [ ] **Step 1: Write the failing test**

```go
// client_test.go
func TestListByTag(t *testing.T) {
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if strings.HasPrefix(r.URL.Path, "/v1/compute/instances") {
            w.Write([]byte(`{"data":[{"instanceId":42,"displayName":"fuzeinfra-elastic-0","status":"running","addresses":{"private":[{"ip":"10.0.0.5"}]},"tags":[{"name":"fuzeinfra-elastic"}]}]}`))
            return
        }
        w.Write([]byte(`{"access_token":"tok","expires_in":300}`)) // token endpoint
    }))
    defer srv.Close()
    c := NewClient(Config{BaseURL: srv.URL})
    got, err := c.ListByTag(context.Background(), "fuzeinfra-elastic")
    if err != nil { t.Fatal(err) }
    if len(got) != 1 || got[0].ID != 42 || got[0].Name != "fuzeinfra-elastic-0" {
        t.Fatalf("unexpected: %+v", got)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd cluster-autoscaler/contabo-externalgrpc && go test ./internal/contabo/ -run TestListByTag -v`
Expected: FAIL — `NewClient` / `ListByTag` undefined.

- [ ] **Step 3: Write minimal implementation**

```go
// client.go — OAuth2 password-grant token, then GET instances, filter by tag.
package contabo

type Config struct{ ClientID, ClientSecret, User, Pass, BaseURL string }
type Instance struct{ ID int64; Name, Status, PrivateIP string; Tags []string }
type CreateReq struct{ Name, ProductID, ImageID, Region string; SSHKeyID int64; UserData string; Tags []string }
type Client interface {
    ListByTag(ctx context.Context, tag string) ([]Instance, error)
    Create(ctx context.Context, req CreateReq) (Instance, error)
    Delete(ctx context.Context, id int64) error
}
type HTTPClient struct{ cfg Config; hc *http.Client; tok string }
func NewClient(cfg Config) *HTTPClient { return &HTTPClient{cfg: cfg, hc: &http.Client{Timeout: 30 * time.Second}} }
// token(): POST cfg.BaseURL/auth/... password grant, cache until expiry.
// ListByTag(): ensure token, GET /v1/compute/instances?page..., unmarshal, keep those whose tags contain tag.
```
Implement `token()`, `ListByTag()` fully; `Create`/`Delete` may be stubs returning `errors.New("not implemented")` for now (Tasks 2 covers them). Add `x-request-id` + `Authorization: Bearer` headers.

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/contabo/ -run TestListByTag -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cluster-autoscaler/contabo-externalgrpc/
git commit -m "feat(ca): Contabo REST client with list-by-tag"
```

---

## Task 2: Contabo client — Create + Delete [MODEL: Haiku]

**Files:**
- Modify: `cluster-autoscaler/contabo-externalgrpc/internal/contabo/client.go`
- Test: `cluster-autoscaler/contabo-externalgrpc/internal/contabo/client_test.go`

**Interfaces:**
- Consumes: `Client`, `CreateReq`, `Instance` from Task 1.
- Produces: working `Create(ctx, CreateReq) (Instance, error)` (POST `/v1/compute/instances`, sets `userData`, `imageId`, `productId`, `region`, applies tags) and `Delete(ctx, id) error` (DELETE `/v1/compute/instances/{id}`).

- [ ] **Step 1: Write the failing test**

```go
func TestCreateAndDelete(t *testing.T) {
    var created, deleted bool
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        switch {
        case r.Method == "POST" && strings.Contains(r.URL.Path, "/instances"):
            created = true
            w.Write([]byte(`{"data":[{"instanceId":99,"displayName":"fuzeinfra-elastic-1","status":"provisioning"}]}`))
        case r.Method == "DELETE" && strings.Contains(r.URL.Path, "/instances/99"):
            deleted = true; w.WriteHeader(204)
        default:
            w.Write([]byte(`{"access_token":"tok","expires_in":300}`))
        }
    }))
    defer srv.Close()
    c := NewClient(Config{BaseURL: srv.URL})
    inst, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-1", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config", Tags: []string{"fuzeinfra-elastic"}})
    if err != nil || inst.ID != 99 { t.Fatalf("create: %v %+v", err, inst) }
    if err := c.Delete(context.Background(), 99); err != nil { t.Fatal(err) }
    if !created || !deleted { t.Fatal("endpoints not hit") }
}
```

- [ ] **Step 2: Run to verify fail** — `go test ./internal/contabo/ -run TestCreateAndDelete -v` → FAIL (`not implemented`).
- [ ] **Step 3: Implement `Create` and `Delete`** — real POST/DELETE with token, JSON body, base64 `userData`, tag application (Contabo tags are a separate assign call if needed — include it), return parsed `Instance`.
- [ ] **Step 4: Run to verify pass** — `go test ./internal/contabo/ -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): Contabo client create/delete"`.

---

## Task 3: gRPC server scaffold + no-op methods [MODEL: Haiku]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/proto/externalgrpc.proto` (vendor from upstream `cluster-autoscaler/cloudprovider/externalgrpc/protos/`), generate `*.pb.go`.
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/provider/server.go`
- Test: `cluster-autoscaler/contabo-externalgrpc/internal/provider/server_test.go`

**Interfaces:**
- Consumes: `contabo.Client` (Task 1/2).
- Produces:
  - `type Config struct { ElasticTag, NamePrefix, ProductID, ImageID, Region string; MinSize, MaxSize int; SSHKeyID int64; UserDataTmpl string }`
  - `type Server struct { cfg Config; cloud contabo.Client }`
  - `func New(cfg Config, cloud contabo.Client) *Server`
  - No-op gRPC methods returning empty responses: `Refresh`, `Cleanup`, `GPULabel`, `GetAvailableGPUTypes`, `PricingNodePrice`, `PricingPodPrice`, `NodeGroupGetOptions`.

- [ ] **Step 1: Write the failing test**

```go
func TestGPULabelEmpty(t *testing.T) {
    s := New(Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}, &fakeCloud{})
    resp, err := s.GPULabel(context.Background(), &protos.GPULabelRequest{})
    if err != nil || resp.Label != "" { t.Fatalf("want empty label, got %q %v", resp.Label, err) }
}
```

- [ ] **Step 2: Run to verify fail** — `go test ./internal/provider/ -run TestGPULabelEmpty -v` → FAIL.
- [ ] **Step 3: Implement** — vendor proto, `protoc`/`buf` generate, `New()`, and each no-op returning `&protos.XResponse{}`. Add `fakeCloud` test double implementing `contabo.Client` in `server_test.go`.
- [ ] **Step 4: Run to verify pass** — PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): externalgrpc server scaffold + no-op methods"`.

---

## Task 4: NodeGroups + NodeGroupForNode (tag classification) [MODEL: Haiku]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/provider/nodegroups.go`
- Test: `cluster-autoscaler/contabo-externalgrpc/internal/provider/nodegroups_test.go`

**Interfaces:**
- Consumes: `Server`, `Config` (Task 3).
- Produces:
  - `NodeGroups(ctx, *NodeGroupsRequest) (*NodeGroupsResponse, error)` → exactly one group `{Id: "elastic", MinSize: cfg.MinSize, MaxSize: cfg.MaxSize}`.
  - `NodeGroupForNode(ctx, *NodeGroupForNodeRequest) (*NodeGroupForNodeResponse, error)` → returns the `elastic` group **only if** the node's provider/name matches an elastic instance (tag `fuzeinfra-elastic`), else an empty group (so baseline/control-plane nodes are "not mine").

- [ ] **Step 1: Failing test**

```go
func TestNodeGroupForNode_BaselineIsForeign(t *testing.T) {
    fc := &fakeCloud{instances: []contabo.Instance{{ID: 1, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}}}}
    s := New(Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}, fc)
    // baseline node -> empty group id
    resp, _ := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: &v1.Node{ObjectMeta: metav1.ObjectMeta{Name: "fuzeinfra-baseline-1"}}})
    if resp.NodeGroup.Id != "" { t.Fatalf("baseline must be foreign, got %q", resp.NodeGroup.Id) }
    // elastic node -> "elastic"
    resp2, _ := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: &v1.Node{ObjectMeta: metav1.ObjectMeta{Name: "fuzeinfra-elastic-0"}}})
    if resp2.NodeGroup.Id != "elastic" { t.Fatalf("elastic node group, got %q", resp2.NodeGroup.Id) }
}
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `NodeGroups` returns the single group; `NodeGroupForNode` calls `cloud.ListByTag(cfg.ElasticTag)` and matches by instance `Name` == node name (elastic names are `<prefix>-<n>`).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): NodeGroups + tag-based NodeGroupForNode"`.

---

## Task 5: NodeGroupTargetSize + NodeGroupNodes [MODEL: Haiku]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/provider/size.go`
- Test: `.../internal/provider/size_test.go`

**Interfaces:**
- Produces:
  - `NodeGroupTargetSize(...)` → count of `fuzeinfra-elastic`-tagged instances.
  - `NodeGroupNodes(...)` → one `Instance{Id: providerID, Status: {InstanceState}}` per elastic instance, mapping Contabo `status` (`provisioning`→creating, `running`→running, deleting→deleting).

- [ ] **Step 1: Failing test** — 2 fake elastic instances → `TargetSize == 2`; `NodeGroupNodes` returns 2 with correct `providerID` (`contabo://<id>`).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** using `cloud.ListByTag`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): NodeGroupTargetSize + NodeGroupNodes"`.

---

## Task 6: NodeGroupIncreaseSize (create + hard cap) [MODEL: Haiku]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/provider/scale.go`
- Test: `.../internal/provider/scale_test.go`

**Interfaces:**
- Consumes: `contabo.Client.Create`, `cfg` (ProductID, ImageID, Region, NamePrefix, ElasticTag, MaxSize, UserDataTmpl, SSHKeyID).
- Produces: `NodeGroupIncreaseSize(ctx, {Id, Delta})` → creates `Delta` instances named `<prefix>-<nextIndex>` with rendered cloud-init userData; **refuses** if `current+Delta > MaxSize` (returns gRPC `codes.OutOfRange`).

- [ ] **Step 1: Failing test**

```go
func TestIncreaseSize_CapEnforced(t *testing.T) {
    fc := &fakeCloud{instances: []contabo.Instance{{ID:1,Name:"fuzeinfra-elastic-0",Tags:[]string{"fuzeinfra-elastic"}}, {ID:2,Name:"fuzeinfra-elastic-1",Tags:[]string{"fuzeinfra-elastic"}}}}
    s := New(Config{ElasticTag:"fuzeinfra-elastic", NamePrefix:"fuzeinfra-elastic", MaxSize:2}, fc)
    _, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{Id:"elastic", Delta:1})
    if status.Code(err) != codes.OutOfRange { t.Fatalf("want OutOfRange, got %v", err) }
    if fc.createCalls != 0 { t.Fatal("must not create beyond cap") }
}
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — compute current from `ListByTag`, enforce cap, render userData from `cfg.UserDataTmpl` (K3S_URL/K3S_TOKEN/wireguard/taint), `Create` each, tag `fuzeinfra-elastic`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): NodeGroupIncreaseSize with hard max cap"`.

---

## Task 7: NodeGroupDeleteNodes + DecreaseTargetSize (delete + floor) [MODEL: Haiku]

**Files:**
- Modify: `cluster-autoscaler/contabo-externalgrpc/internal/provider/scale.go`
- Test: `.../internal/provider/scale_test.go`

**Interfaces:**
- Produces: `NodeGroupDeleteNodes(ctx, {Id, Nodes[]})` → maps each node providerID → instance id → `cloud.Delete`; never deletes a non-elastic instance (guard by tag). `NodeGroupDecreaseTargetSize` → no-op success (Contabo has no reservation concept; return `&protos.NodeGroupDecreaseTargetSizeResponse{}`).

- [ ] **Step 1: Failing test** — deleting an elastic node calls `cloud.Delete(id)`; attempting to delete a node whose id is not in the elastic set returns `codes.InvalidArgument` and calls Delete 0 times.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — parse `contabo://<id>` providerID, verify membership via `ListByTag`, delete.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): NodeGroupDeleteNodes guarded by elastic tag"`.

---

## Task 8: NodeGroupTemplateNodeInfo (scale-from-zero) [MODEL: Sonnet]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/internal/provider/template.go`
- Test: `.../internal/provider/template_test.go`

**Interfaces:**
- Produces: `NodeGroupTemplateNodeInfo(ctx, {Id})` → a synthetic `*v1.Node` describing an elastic node's capacity (CPU/mem from the product spec map), labels (`fuzeinfra.io/pool=elastic`), and taint (`fuzeinfra.io/elastic=true:PreferNoSchedule`). **Required because `min=0`** — CA needs the template to decide whether a pending pod would fit on a *would-be* elastic node.

- [ ] **Step 1: Failing test** — returns a Node with `status.capacity["cpu"]` and `["memory"]` matching the configured product (e.g. V45 = 6 vCPU / 16Gi), label present, taint present.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — a `productSpecs map[string]struct{CPU, MemGi int}` config; build the Node with capacity/allocatable, labels, taints, and `providerID` empty (template).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): TemplateNodeInfo for scale-from-zero"`.

> **Model note:** Sonnet — scale-from-zero templates are the classic subtle correctness trap (wrong capacity/labels/taints silently break scheduling decisions). Needs judgment about the product-spec mapping and taint/label parity with cloud-init.

---

## Task 9: Wire server + main + Dockerfile [MODEL: Sonnet]

**Files:**
- Modify: `cluster-autoscaler/contabo-externalgrpc/internal/provider/server.go` (register all methods)
- Create: `cluster-autoscaler/contabo-externalgrpc/cmd/server/main.go`
- Create: `cluster-autoscaler/contabo-externalgrpc/Dockerfile`
- Test: `.../cmd/server/main_test.go` (config parsing) + `.../internal/provider/integration_test.go` (in-proc gRPC round-trip)

**Interfaces:**
- Consumes: all Task 1–8 methods.
- Produces: a runnable binary reading config from env (`CONTABO_*`, `K3S_SERVER_URL`, `K3S_NODE_TOKEN`, `ELASTIC_*`, `MIN_SIZE`, `MAX_SIZE`, `PRODUCT_ID`, `IMAGE_ID`, `REGION`, `SSH_KEY_ID`) and serving `protos.RegisterCloudProviderServer` on `:8086`.

- [ ] **Step 1: Failing test** — in-proc: start server with `fakeCloud`, dial via `bufconn`, call `NodeGroups` → get `elastic`; call `IncreaseSize delta=1` → fake `createCalls==1`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** `main.go` (config load, `net.Listen`, `grpc.NewServer`, register, graceful stop) + `Dockerfile` (multi-stage `golang:1.22` → `gcr.io/distroless/static`).
- [ ] **Step 4: Run to verify pass** — `go test ./... -v` all green; `docker build` succeeds.
- [ ] **Step 5: Commit** — `git commit -am "feat(ca): wire externalgrpc server, main, Dockerfile"`.

> **Model note:** Sonnet — this is the aggregation task that consumes every Haiku-built block; needs whole-module context.

---

## Task 10: CI build + push provider image [MODEL: Haiku]

**Files:**
- Modify: `.github/workflows/` build matrix (add `contabo-externalgrpc` image build/push to the registry used by other FuzeInfra images).
- Test: workflow dry-run via `act` or a `-n` push.

**Interfaces:**
- Consumes: `Dockerfile` (Task 9).
- Produces: image `ghcr.io/izzywdev/fuzeinfra-contabo-ca-provider:<tag>` pushed on merge to main.

- [ ] **Step 1** Add a build job mirroring the existing image jobs (same registry/login/tag scheme). — [ ] **Step 2** Validate YAML (`yamllint` / existing `helm-validate`-style check). — [ ] **Step 3** Commit `git commit -am "ci(ca): build+push contabo CA provider image"`.

---

## Task 11: Helm — PriorityClass + pause-pod overprovisioning [MODEL: Haiku]

**Files:**
- Create: `helm/fuzeinfra/templates/autoscaler/priorityclass.yaml`
- Create: `helm/fuzeinfra/templates/autoscaler/overprovisioning.yaml`
- Modify: `helm/fuzeinfra/values.yaml` (add `overprovisioning:` block)

**Interfaces:**
- Produces: a `PriorityClass fuzeinfra-overprovisioning` (value `-10`, `globalDefault: false`) and a Deployment of `registry.k8s.io/pause` pods that tolerate the elastic taint + `nodeAffinity` to `fuzeinfra.io/pool=elastic`, sized `{{ .Values.overprovisioning.replicas }}` × `{{ .Values.overprovisioning.resources }}`, all gated on `{{- if .Values.overprovisioning.enabled }}`.

- [ ] **Step 1: Write the manifests** (complete YAML, templated, behind the gate; default `enabled: false`, `replicas: 1`, request ≈ one elastic node's schedulable share).
- [ ] **Step 2: Validate** — `helm template helm/fuzeinfra -f helm/fuzeinfra/values-contabo.yaml | kubeconform -strict -ignore-missing-schemas`. Expected: no errors; objects only rendered when enabled.
- [ ] **Step 3: Commit** — `git commit -am "feat(ca): overprovisioning pause-pods + priorityclass"`.

---

## Task 12: Helm — provider Deployment + Service + secrets [MODEL: Haiku]

**Files:**
- Create: `helm/fuzeinfra/templates/autoscaler/provider-deployment.yaml`
- Modify: `helm/fuzeinfra/values.yaml` (`clusterAutoscaler.provider` block)

**Interfaces:**
- Consumes: provider image (Task 10), CI secrets.
- Produces: Deployment `fuzeinfra-contabo-ca-provider` (envFrom a Secret carrying `CONTABO_*`/`K3S_*`/`ELASTIC_*`), Service `contabo-ca-provider:8086`, gated on `clusterAutoscaler.enabled`.

- [ ] **Step 1** Write Deployment+Service (env from `existingSecret`, resource limits, liveness on gRPC port). — [ ] **Step 2** `helm template … | kubeconform -strict -ignore-missing-schemas` passes. — [ ] **Step 3** `git commit -am "feat(ca): provider deployment + service"`.

---

## Task 13: Helm — Cluster Autoscaler Deployment + RBAC + arg tuning [MODEL: Sonnet]

**Files:**
- Create: `helm/fuzeinfra/templates/autoscaler/cluster-autoscaler.yaml`
- Create: `helm/fuzeinfra/templates/autoscaler/_autoscaler-helpers.tpl`
- Modify: `helm/fuzeinfra/values.yaml`, `values-contabo.yaml`, `values-local.yaml`

**Interfaces:**
- Consumes: provider Service (Task 12).
- Produces: CA Deployment (image `registry.k8s.io/autoscaling/cluster-autoscaler`), ServiceAccount + ClusterRole/Binding (upstream CA RBAC), args:
  `--cloud-provider=externalgrpc`, `--cloud-config=<grpc addr contabo-ca-provider:8086>`, `--nodes=0:2:elastic`, `--scale-down-enabled=true`, `--scale-down-unneeded-time=45m`, `--scale-down-delay-after-add=20m`, `--max-node-provision-time=15m`, `--max-total-unready-percentage=45`, `--expander=least-waste`, `--balance-similar-node-groups=false`. `values.yaml` gate `clusterAutoscaler.enabled: false`; `values-contabo.yaml` sets `true` + real image tag; `values-local.yaml` stays `false`.

- [ ] **Step 1** Write CA Deployment + full upstream RBAC (copy the documented CA ClusterRole verbatim) + helpers. — [ ] **Step 2** `helm template helm/fuzeinfra -f values-contabo.yaml | kubeconform -strict -ignore-missing-schemas` and `helm lint`. Expected pass. — [ ] **Step 3** Confirm nothing renders under `values-local.yaml`. — [ ] **Step 4** `git commit -am "feat(ca): cluster-autoscaler deployment + RBAC + Contabo tuning"`.

> **Model note:** Sonnet — CA arg tuning + RBAC is judgment-heavy and prod-affecting; wrong RBAC or a bad `--scale-down` knob is a real incident.

---

## Task 14: Scheduling guardrails — stateful safe-to-evict + elastic taint [MODEL: Haiku]

**Files:**
- Modify: `helm/fuzeinfra/templates/databases.yaml` and other stateful workload templates (Prometheus, Kafka, ES, Neo4j, Mongo) — add pod annotation `cluster-autoscaler.kubernetes.io/safe-to-evict: "false"`.
- Modify: `modules/contabo-k3s-node/cloud-init.tftpl` — for `role=elastic`, emit `--node-taint fuzeinfra.io/elastic=true:PreferNoSchedule` and `--node-label fuzeinfra.io/pool=elastic`.

**Interfaces:**
- Produces: stateful pods CA will never evict; elastic nodes only accept toleration-bearing stateless workloads.

- [ ] **Step 1** Add the annotation to each stateful pod template; add taint/label to cloud-init for the elastic role. — [ ] **Step 2** `helm template … | kubeconform -strict -ignore-missing-schemas`; `terraform fmt -check modules/contabo-k3s-node`. — [ ] **Step 3** `git commit -am "feat(ca): stateful safe-to-evict + elastic node taint/label"`.

---

## Task 15: Terraform — WireGuard overlay + 3-node baseline + elastic exclusion [MODEL: Sonnet]

**Files:**
- Modify: `terraform/contabo/provisioning.tf` — add `--flannel-backend=wireguard-native` to the k3s server install (open `51820/udp` in `vps.tf` ufw).
- Create: `terraform/contabo/baseline.tf` — 2 baseline worker nodes via `modules/contabo-k3s-node` (`role=baseline`), joined to the control-plane (total baseline = 3 incl. control-plane).
- Modify: `terraform/contabo/*` — ensure a `lifecycle { ignore_changes }` / tag filter so no `fuzeinfra-elastic`-tagged instance is ever adopted.

**Interfaces:**
- Produces: prod cluster on WireGuard overlay with a stable 3-node baseline; TF blind to elastic instances.

- [ ] **Step 1** Write the changes; `terraform init && terraform validate && terraform plan` (against prod state) — review that plan shows ONLY the intended baseline additions + flannel change and **no** destroys. — [ ] **Step 2** `git commit -am "feat(ca): wireguard overlay + 3-node TF baseline + elastic exclusion"`.

> **Model note:** Sonnet drafts; **Opus reviews the `terraform plan` before any apply** — this is prod-touching (overlay cutover can disrupt pod networking; apply is orchestrator-gated, not automated). See Task 18.

---

## Task 16: CI — Terraform elastic-drift guard [MODEL: Haiku]

**Files:**
- Create: `.github/workflows/tf-elastic-drift-check.yml`

**Interfaces:**
- Produces: a PR check that runs `terraform plan -json` and fails if any planned action targets a `fuzeinfra-elastic`-tagged instance (asserts the exclusion holds).

- [ ] **Step 1** Write the workflow (plan → grep/jq for `fuzeinfra-elastic` in resource changes → fail if present). — [ ] **Step 2** `yamllint` the workflow. — [ ] **Step 3** `git commit -am "ci(ca): guard against TF touching elastic nodes"`.

---

## Task 17: Integration harness on kind (fake cloud) [MODEL: Sonnet]

**Files:**
- Create: `cluster-autoscaler/contabo-externalgrpc/test/integration/kind_test.sh` + a `fakeCloud` gRPC provider build target.
- Create: `cluster-autoscaler/contabo-externalgrpc/test/integration/README.md`

**Interfaces:**
- Consumes: the provider binary (with an in-memory fake Contabo backend), CA Deployment.
- Produces: a repeatable local test proving: unschedulable pod → `IncreaseSize` called; low utilization → `DeleteNodes` called; baseline node never selected; cap=2 respected.

- [ ] **Step 1** Write a fake-backed provider mode (env `FAKE_CLOUD=1` uses an in-memory instance list instead of real Contabo). — [ ] **Step 2** Script: `kind create` (single node OK for the control loop), `helm install` with `clusterAutoscaler.enabled=true` + provider in fake mode, apply a Deployment that can't fit → assert provider logs `IncreaseSize`. — [ ] **Step 3** Assert scale-down path with a drained node. — [ ] **Step 4** `git commit -am "test(ca): kind integration harness with fake cloud"`.

> **Model note:** Sonnet — multi-component wiring (kind + CA + provider + assertions); needs system-level reasoning. Also gated by the known **kind cgroup-v1 blocker on this dev machine** — run in CI or a WSL2 engine, per project memory.

---

## Task 18: Prod prerequisites cutover + gated staging e2e [MODEL: Opus/orchestrator]

**Files:** none new — this is execution/verification, driven through GitOps.

**Steps (orchestrator-owned, NOT delegated to a cheaper model):**
- [ ] **Step 1** Merge Task 15 (WireGuard + baseline) via PR; watch Argo sync; verify pod networking healthy post-overlay-change (`kubectl -n fuzeinfra get pods`, cross-node ping) before proceeding.
- [ ] **Step 2** Confirm the 3-node baseline is `Ready` and stateful pods stayed put.
- [ ] **Step 3** Merge the autoscaler stack (Tasks 11–14, 16) with `clusterAutoscaler.enabled: true` in `values-contabo.yaml`; watch Argo sync.
- [ ] **Step 4** Gated staging spike test: apply load, watch a real elastic Contabo node provision + join (tainted) + absorb load; then idle and watch cordon+drain+delete. Verify `terraform plan` stays clean throughout (the drift guard should already enforce this).
- [ ] **Step 5** Confirm floor: with zero load, cluster returns to exactly 3 baseline nodes; ceiling: under sustained load it stops at 5 total.
- [ ] **Step 6** Update `docs/K3S_SECOND_NODE_RUNBOOK.md` / add an autoscaling runbook section documenting the live behavior and knobs.

---

## Self-Review

**Spec coverage:**
- §4 Approach A′ (CA + externalgrpc + pause-pods) → Tasks 3–13, 11. ✓
- §5 TF/CA boundary + tag exclusion → Tasks 4, 7, 15, 16. ✓
- §6 five components → client (1–2), provider (3–9), pause-pods (11), guardrails (14), TF/overlay (15). ✓
- §7 data flows → exercised by Tasks 6/7 + 17 + 18. ✓
- §8 failure modes: cap (6), floor/guard (7,15), drift (16), safe-to-evict (14), slow-provision tuning (13), scale-from-zero (8). ✓ (orphan reaper folded into provider `NodeGroupNodes` status + CA unready handling; if a standalone reaper is wanted, add as Task 7b — noted.)
- §9 testing → unit (1–9), integration (17), gated e2e (18). ✓
- §10 prerequisites (WireGuard, 3-node baseline, secrets reuse, GitOps) → Tasks 15, 18, 12, 13/Argo. ✓

**Placeholder scan:** no TBD/TODO; each code step carries real code or a precise manifest/arg spec. (Two spots intentionally reference "copy upstream verbatim" — the CA proto in Task 3 and the CA RBAC in Task 13 — because vendoring the canonical upstream artifact is correct here, not a placeholder.)

**Type consistency:** `contabo.Client` interface (`ListByTag`/`Create`/`Delete`), `Instance`, `CreateReq`, `Config`, and the `elastic` group id are used consistently across Tasks 1–9. Provider IDs use `contabo://<id>` in Tasks 5 and 7. ✓

---

## Execution Handoff

Model-aware execution: Opus orchestrates; Haiku builds the bounded blocks (Tasks 1–7, 10–12, 14, 16); Sonnet aggregates/wires (Tasks 8, 9, 13, 15, 17); Opus owns the prod cutover (Task 18) and every review gate.
