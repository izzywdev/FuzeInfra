package provider_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
)

func TestNodeGroupTargetSize_ReturnsElasticCount(t *testing.T) {
	fc := &fakeCloud{
		instances: []contabo.Instance{
			{ID: 100, Name: "elastic-node-1", Status: "running", Tags: []string{"fuzeinfra-elastic"}},
			{ID: 101, Name: "elastic-node-2", Status: "provisioning", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupTargetSize(context.Background(), &protos.NodeGroupTargetSizeRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTargetSize error: %v", err)
	}

	if resp.TargetSize != 2 {
		t.Fatalf("want TargetSize=2, got %d", resp.TargetSize)
	}
}

func TestNodeGroupNodes_ReturnsInstancesWithCorrectMapping(t *testing.T) {
	fc := &fakeCloud{
		instances: []contabo.Instance{
			{ID: 100, Name: "elastic-node-1", Status: "running", Tags: []string{"fuzeinfra-elastic"}},
			{ID: 101, Name: "elastic-node-2", Status: "provisioning", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupNodes error: %v", err)
	}

	if len(resp.Instances) != 2 {
		t.Fatalf("want 2 instances, got %d", len(resp.Instances))
	}

	// Check instance 1 (running)
	inst1 := resp.Instances[0]
	if inst1.Id != "contabo://elastic-node-1" {
		t.Fatalf("instance 1: want Id=contabo://elastic-node-1, got %q", inst1.Id)
	}
	if inst1.Status == nil {
		t.Fatalf("instance 1: Status is nil")
	}
	if inst1.Status.InstanceState != protos.InstanceStatus_instanceRunning {
		t.Fatalf("instance 1: want instanceRunning, got %d", inst1.Status.InstanceState)
	}
	if inst1.Status.ErrorInfo != nil {
		t.Fatalf("instance 1: ErrorInfo should be nil, got %v", inst1.Status.ErrorInfo)
	}

	// Check instance 2 (provisioning -> creating)
	inst2 := resp.Instances[1]
	if inst2.Id != "contabo://elastic-node-2" {
		t.Fatalf("instance 2: want Id=contabo://elastic-node-2, got %q", inst2.Id)
	}
	if inst2.Status == nil {
		t.Fatalf("instance 2: Status is nil")
	}
	if inst2.Status.InstanceState != protos.InstanceStatus_instanceCreating {
		t.Fatalf("instance 2: want instanceCreating, got %d", inst2.Status.InstanceState)
	}
	if inst2.Status.ErrorInfo != nil {
		t.Fatalf("instance 2: ErrorInfo should be nil, got %v", inst2.Status.ErrorInfo)
	}
}

func TestNodeGroupNodes_StateMapping(t *testing.T) {
	tests := []struct {
		name          string
		contaboStatus string
		protoState    protos.InstanceStatus_InstanceState
	}{
		{"provisioning", "provisioning", protos.InstanceStatus_instanceCreating},
		{"installing", "installing", protos.InstanceStatus_instanceCreating},
		{"pending", "pending", protos.InstanceStatus_instanceCreating},
		{"running", "running", protos.InstanceStatus_instanceRunning},
		{"ready", "ready", protos.InstanceStatus_instanceRunning},
		{"deleting", "deleting", protos.InstanceStatus_instanceDeleting},
		{"deleted", "deleted", protos.InstanceStatus_instanceDeleting},
		{"stopping", "stopping", protos.InstanceStatus_instanceDeleting},
		{"Running (title case)", "Running", protos.InstanceStatus_instanceRunning},
		{"RUNNING (uppercase)", "RUNNING", protos.InstanceStatus_instanceRunning},
		{" running (with spaces)", " running ", protos.InstanceStatus_instanceRunning},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			fc := &fakeCloud{
				instances: []contabo.Instance{
					{ID: 42, Name: "test-node", Status: tt.contaboStatus, Tags: []string{"fuzeinfra-elastic"}},
				},
			}
			cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
			s := provider.New(cfg, fc)

			resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{Id: "elastic"})
			if err != nil {
				t.Fatalf("NodeGroupNodes error: %v", err)
			}

			if len(resp.Instances) != 1 {
				t.Fatalf("want 1 instance, got %d", len(resp.Instances))
			}

			if resp.Instances[0].Status.InstanceState != tt.protoState {
				t.Fatalf("want %v, got %v", tt.protoState, resp.Instances[0].Status.InstanceState)
			}
		})
	}
}

func TestNodeGroupTargetSize_ListErrorPropagates(t *testing.T) {
	fc := &fakeCloudWithError{listErr: errors.New("transient API error")}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupTargetSize(context.Background(), &protos.NodeGroupTargetSizeRequest{Id: "elastic"})
	if err == nil {
		t.Fatalf("want error, got nil")
	}
	if resp != nil {
		t.Fatalf("want nil response on error, got %v", resp)
	}
}

func TestNodeGroupNodes_ListErrorPropagates(t *testing.T) {
	fc := &fakeCloudWithError{listErr: errors.New("transient API error")}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{Id: "elastic"})
	if err == nil {
		t.Fatalf("want error, got nil")
	}
	if resp != nil {
		t.Fatalf("want nil response on error, got %v", resp)
	}
}

func TestNodeGroupNodes_EmptyReturnsNoError(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{}}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupNodes error: %v", err)
	}
	if resp == nil {
		t.Fatalf("want non-nil response on success")
	}
	if len(resp.Instances) != 0 {
		t.Fatalf("want 0 instances, got %d", len(resp.Instances))
	}
}

func TestNodeGroupTargetSize_EmptyReturnsZero(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{}}
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 10}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupTargetSize(context.Background(), &protos.NodeGroupTargetSizeRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTargetSize error: %v", err)
	}
	if resp == nil {
		t.Fatalf("want non-nil response on success")
	}
	if resp.TargetSize != 0 {
		t.Fatalf("want TargetSize=0, got %d", resp.TargetSize)
	}
}

// --- Cancelled-instance convergence guards ---------------------------------
//
// Contabo has no immediate-terminate API: Delete() only schedules removal at
// the end of the paid month, and until then the instance keeps running and
// keeps matching ListByNamePrefix. Without filtering, the pool would re-wedge
// at MaxSize on the FIRST reclaim after the NodeGroupForNode fix — unwedged
// once, then stuck again for up to a month. See liveElasticInstances.

func cancelledInst(id int64, name string) contabo.Instance {
	return contabo.Instance{
		ID: id, Name: name, Status: "running",
		CancelDate: time.Date(2026, 9, 30, 0, 0, 0, 0, time.UTC),
	}
}

// TestNodeGroupTargetSize_ExcludesCancelled is the convergence guard: a pool
// whose instances are all cancelled must report target size 0, not MaxSize,
// or CA can never replace the capacity it just released.
func TestNodeGroupTargetSize_ExcludesCancelled(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		cancelledInst(1, "fuzeinfra-prod-elastic-v2-aaaaaaaa"),
		cancelledInst(2, "fuzeinfra-prod-elastic-v2-bbbbbbbb"),
		{ID: 3, Name: "fuzeinfra-prod-elastic-v2-cccccccc", Status: "running"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2", ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize: 0, MaxSize: 4,
	}, fc)

	resp, err := s.NodeGroupTargetSize(context.Background(), &protos.NodeGroupTargetSizeRequest{})
	if err != nil {
		t.Fatalf("NodeGroupTargetSize error: %v", err)
	}
	if resp.TargetSize != 1 {
		t.Fatalf("cancelled instances must not hold a pool slot: want targetSize=1, got %d "+
			"(counting them pins the group at MaxSize and CA logs \"max size reached\")", resp.TargetSize)
	}
}

// TestNodeGroupNodes_ExcludesCancelled: a cancelled instance is already on
// Contabo's termination path, so CA must not keep it as group membership.
func TestNodeGroupNodes_ExcludesCancelled(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		cancelledInst(1, "fuzeinfra-prod-elastic-v2-aaaaaaaa"),
		{ID: 2, Name: "fuzeinfra-prod-elastic-v2-bbbbbbbb", Status: "running"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2", ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize: 0, MaxSize: 4,
	}, fc)

	resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{})
	if err != nil {
		t.Fatalf("NodeGroupNodes error: %v", err)
	}
	if len(resp.Instances) != 1 || resp.Instances[0].Id != "contabo://fuzeinfra-prod-elastic-v2-bbbbbbbb" {
		t.Fatalf("want only the live instance reported, got %v", resp.Instances)
	}
}

// TestNodeGroupNodes_StillReportsUncancelledOrphan is the anti-stranding
// guard. A never-joined instance we did NOT cancel must STILL be reported, so
// CA can attribute and reclaim it. Hiding it would unwedge scale-up while
// leaking a billed VPS invisible to every reclaim path — strictly worse than
// the bug being fixed.
func TestNodeGroupNodes_StillReportsUncancelledOrphan(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 203547162, Name: "fuzeinfra-prod-elastic-v2-adca9568", Status: "running"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2", ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize: 0, MaxSize: 4,
	}, fc)

	resp, err := s.NodeGroupNodes(context.Background(), &protos.NodeGroupNodesRequest{})
	if err != nil {
		t.Fatalf("NodeGroupNodes error: %v", err)
	}
	if len(resp.Instances) != 1 {
		t.Fatalf("an uncancelled orphan must remain visible to CA so it can be reclaimed, got %v", resp.Instances)
	}
}

// TestNodeGroupIncreaseSize_CancelledDoesNotBindCap: the pool must be able to
// replace capacity it released. Cancelled instances do not bind the cap...
func TestNodeGroupIncreaseSize_CancelledDoesNotBindCap(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		cancelledInst(1, "fuzeinfra-prod-elastic-v2-aaaaaaaa"),
		cancelledInst(2, "fuzeinfra-prod-elastic-v2-bbbbbbbb"),
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2", ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize: 0, MaxSize: 2,
	}, fc)

	if _, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{Delta: 1}); err != nil {
		t.Fatalf("cancelled instances must not bind the cap: %v", err)
	}

	// ...but they MUST still reserve their display names, because Contabo
	// keeps a cancelled instance's name until it is actually terminated and
	// 400s on a duplicate.
	for _, inst := range fc.instances {
		if inst.Name == "fuzeinfra-prod-elastic-v2-aaaaaaaa" && inst.ID != 1 {
			t.Fatalf("a new instance reused a cancelled instance's display name: %+v", inst)
		}
	}
}

// TestNodeGroupIncreaseSize_UncancelledOrphanStillBindsCap proves the filter
// did not reopen the untagged-orphan runaway hole that cost real money: an
// instance we never cancelled still counts against MaxSize.
func TestNodeGroupIncreaseSize_UncancelledOrphanStillBindsCap(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 1, Name: "fuzeinfra-prod-elastic-v2-aaaaaaaa", Status: "running"},
		{ID: 2, Name: "fuzeinfra-prod-elastic-v2-bbbbbbbb", Status: "running"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2", ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize: 0, MaxSize: 2,
	}, fc)

	if _, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{Delta: 1}); err == nil {
		t.Fatal("uncancelled instances at MaxSize must still refuse scale-up (anti-runaway cap)")
	}
}
