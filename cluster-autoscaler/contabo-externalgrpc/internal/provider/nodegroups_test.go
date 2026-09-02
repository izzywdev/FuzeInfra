package provider_test

import (
	"context"
	"errors"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
)

func TestNodeGroups_ReturnsOneGroup(t *testing.T) {
	cfg := provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroups(context.Background(), &protos.NodeGroupsRequest{})
	if err != nil {
		t.Fatalf("NodeGroups error: %v", err)
	}

	if len(resp.NodeGroups) != 1 {
		t.Fatalf("want 1 node group, got %d", len(resp.NodeGroups))
	}

	ng := resp.NodeGroups[0]
	if ng.Id != "elastic" {
		t.Fatalf("want id=elastic, got %q", ng.Id)
	}
	if ng.MinSize != 0 {
		t.Fatalf("want minSize=0, got %d", ng.MinSize)
	}
	if ng.MaxSize != 2 {
		t.Fatalf("want maxSize=2, got %d", ng.MaxSize)
	}
}

func TestNodeGroupForNode_BaselineIsForeign(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{{ID: 1, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}}}}
	s := provider.New(provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}, fc)

	// baseline node -> empty group id
	resp, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: &protos.ExternalGrpcNode{Name: "fuzeinfra-baseline-1"}})
	if err != nil {
		t.Fatalf("NodeGroupForNode error: %v", err)
	}
	if resp.NodeGroup.Id != "" {
		t.Fatalf("baseline must be foreign, got %q", resp.NodeGroup.Id)
	}

	// elastic node -> "elastic"
	resp2, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: &protos.ExternalGrpcNode{Name: "fuzeinfra-elastic-0"}})
	if err != nil {
		t.Fatalf("NodeGroupForNode error: %v", err)
	}
	if resp2.NodeGroup.Id != "elastic" {
		t.Fatalf("elastic node group, got %q", resp2.NodeGroup.Id)
	}
}

func TestNodeGroupForNode_ListErrorPropagates(t *testing.T) {
	fc := &fakeCloudWithError{listErr: errors.New("transient API error")}
	s := provider.New(provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}, fc)

	resp, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: &protos.ExternalGrpcNode{Name: "any-node"}})
	if err == nil {
		t.Fatalf("want error, got nil")
	}
	if resp != nil {
		t.Fatalf("want nil response on error, got %v", resp)
	}
}

// fakeCloudWithError is a test double that allows error injection in
// ListByTag/ListByNamePrefix.
type fakeCloudWithError struct {
	listErr error
}

func (f *fakeCloudWithError) ListByTag(_ context.Context, _ string) ([]contabo.Instance, error) {
	return nil, f.listErr
}

func (f *fakeCloudWithError) ListByNamePrefix(_ context.Context, _ string) ([]contabo.Instance, error) {
	return nil, f.listErr
}

func (f *fakeCloudWithError) Create(_ context.Context, req contabo.CreateReq) (contabo.Instance, error) {
	return contabo.Instance{}, errors.New("not implemented in test double")
}

func (f *fakeCloudWithError) Delete(_ context.Context, id int64) error {
	return errors.New("not implemented in test double")
}

// --- Regression guards for the 2026-09-02 scale-up wedge -------------------
//
// These pin the exact defect that starved CI fleet-wide: CA passes a
// SYNTHETIC "fake node" for any instance the provider reported that has no
// k8s Node object, and on that object BOTH Name and ProviderID are the raw
// instance Id ("contabo://<name>") — there is no bare name anywhere. The old
// `inst.Name == req.Node.Name` comparison therefore returned an empty
// NodeGroup, CA logged "No node group for node contabo://..., skipping" and
// could never reclaim the never-joined instances, which permanently pinned
// NodeGroupTargetSize at MaxSize ("Skipping node group elastic - max size
// reached"). See NodeGroupForNode's doc comment.

// TestNodeGroupForNode_UnregisteredFakeNodeIsElastic is THE guard: a node
// whose Name is scheme-qualified (CA's fake node for a never-joined
// instance) must still be attributed to the elastic group, or CA's
// removeOldUnregisteredNodes can never garbage-collect it.
func TestNodeGroupForNode_UnregisteredFakeNodeIsElastic(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 203547162, Name: "fuzeinfra-prod-elastic-v2-adca9568"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2",
		ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize:    0, MaxSize: 4,
	}, fc)

	// Exactly what CA sends for an unregistered instance: scheme-qualified
	// in BOTH fields, because it is fabricated from the Instance alone.
	resp, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{
		Node: &protos.ExternalGrpcNode{
			Name:       "contabo://fuzeinfra-prod-elastic-v2-adca9568",
			ProviderID: "contabo://fuzeinfra-prod-elastic-v2-adca9568",
		},
	})
	if err != nil {
		t.Fatalf("NodeGroupForNode error: %v", err)
	}
	if resp.NodeGroup.GetId() != "elastic" {
		t.Fatalf("unregistered fake node must map to the elastic group so CA can reclaim it; got id=%q "+
			"(this is the wedge: CA logs \"No node group for node contabo://...\" and never scales up)",
			resp.NodeGroup.GetId())
	}
}

// TestNodeGroupForNode_RegisteredBareNameStillElastic guards the other half:
// the fix must not regress the ordinary registered-node path, where Name is
// the bare k3s --node-name.
func TestNodeGroupForNode_RegisteredBareNameStillElastic(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 203548713, Name: "fuzeinfra-prod-elastic-v2-c056a22a"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2",
		ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize:    0, MaxSize: 4,
	}, fc)

	resp, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{
		Node: &protos.ExternalGrpcNode{
			Name:       "fuzeinfra-prod-elastic-v2-c056a22a",
			ProviderID: "contabo://fuzeinfra-prod-elastic-v2-c056a22a",
		},
	})
	if err != nil {
		t.Fatalf("NodeGroupForNode error: %v", err)
	}
	if resp.NodeGroup.GetId() != "elastic" {
		t.Fatalf("registered elastic node must map to the elastic group; got id=%q", resp.NodeGroup.GetId())
	}
}

// TestNodeGroupForNode_ForeignNodeStillRefused proves the scheme-stripping
// did not widen membership: a non-elastic node must still resolve to no
// group, however it is addressed. This is the guard that keeps
// NodeGroupDeleteNodes from ever being pointed at a control-plane node.
func TestNodeGroupForNode_ForeignNodeStillRefused(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 1, Name: "fuzeinfra-prod-elastic-v2-c056a22a"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2",
		ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize:    0, MaxSize: 4,
	}, fc)

	for _, node := range []*protos.ExternalGrpcNode{
		{Name: "vmi3383846", ProviderID: "contabo://vmi3383846"},
		{Name: "contabo://vmi3383846", ProviderID: "contabo://vmi3383846"},
		{Name: "fuzeinfra-ci-runner-1"},
	} {
		resp, err := s.NodeGroupForNode(context.Background(), &protos.NodeGroupForNodeRequest{Node: node})
		if err != nil {
			t.Fatalf("NodeGroupForNode(%q) error: %v", node.GetName(), err)
		}
		if resp.NodeGroup.GetId() != "" {
			t.Fatalf("foreign node %q must not be elastic; got id=%q", node.GetName(), resp.NodeGroup.GetId())
		}
	}
}

// TestNodeGroupDeleteNodes_ReclaimsUnregisteredFakeNode closes the loop: once
// NodeGroupForNode attributes the never-joined instance, CA calls
// NodeGroupDeleteNodes with that same synthetic node. The delete must resolve
// it to the real numeric Contabo id, or the pool unwedges in the logs but
// keeps paying for the leaked VPS.
func TestNodeGroupDeleteNodes_ReclaimsUnregisteredFakeNode(t *testing.T) {
	fc := &fakeCloud{instances: []contabo.Instance{
		{ID: 203543503, Name: "fuzeinfra-prod-elastic-v2-588198fe"},
		{ID: 203548713, Name: "fuzeinfra-prod-elastic-v2-c056a22a"},
	}}
	s := provider.New(provider.Config{
		NamePrefix: "fuzeinfra-prod-elastic-v2",
		ElasticTag: "fuzeinfra-prod-elastic-v2",
		MinSize:    0, MaxSize: 4,
	}, fc)

	_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
		Nodes: []*protos.ExternalGrpcNode{{
			Name:       "contabo://fuzeinfra-prod-elastic-v2-588198fe",
			ProviderID: "contabo://fuzeinfra-prod-elastic-v2-588198fe",
		}},
	})
	if err != nil {
		t.Fatalf("NodeGroupDeleteNodes on an unregistered fake node must succeed: %v", err)
	}
	if len(fc.deleted) != 1 || fc.deleted[0] != 203543503 {
		t.Fatalf("want the never-joined instance 203543503 deleted by numeric id, got %v", fc.deleted)
	}
}
