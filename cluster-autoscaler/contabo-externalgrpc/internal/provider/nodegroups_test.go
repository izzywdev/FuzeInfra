package provider_test

import (
	"context"
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
