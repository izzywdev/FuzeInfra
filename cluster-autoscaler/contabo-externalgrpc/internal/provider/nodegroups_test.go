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

// fakeCloudWithError is a test double that allows error injection in ListByTag.
type fakeCloudWithError struct {
	listErr error
}

func (f *fakeCloudWithError) ListByTag(_ context.Context, _ string) ([]contabo.Instance, error) {
	return nil, f.listErr
}

func (f *fakeCloudWithError) Create(_ context.Context, req contabo.CreateReq) (contabo.Instance, error) {
	return contabo.Instance{}, errors.New("not implemented in test double")
}

func (f *fakeCloudWithError) Delete(_ context.Context, id int64) error {
	return errors.New("not implemented in test double")
}
