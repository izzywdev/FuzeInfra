package provider_test

import (
	"context"
	"errors"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestIncreaseSize_CapEnforced(t *testing.T) {
	// Pre-load fakeCloud with 2 existing elastic instances
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 1, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}},
			{ID: 2, Name: "fuzeinfra-elastic-1", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
		NamePrefix: "fuzeinfra-elastic",
		MaxSize:    2,
	}
	s := provider.New(cfg, fc)

	// Try to increase by 1 when already at max (2) -> should fail
	_, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 1,
	})

	// Must return OutOfRange error
	if status.Code(err) != codes.OutOfRange {
		t.Fatalf("want OutOfRange, got %v", err)
	}

	// Must not create any instances
	if fc.createCalls != 0 {
		t.Fatalf("must not create beyond cap, but got %d create calls", fc.createCalls)
	}
}

func TestIncreaseSize_HappyPath(t *testing.T) {
	// Start with 0 instances, MaxSize=2
	fc := &fakeCloudCapTest{instances: []contabo.Instance{}}
	cfg := provider.Config{
		ElasticTag:   "fuzeinfra-elastic",
		NamePrefix:   "fuzeinfra-elastic",
		ProductID:    "prod-123",
		ImageID:      "img-456",
		Region:       "us-central",
		SSHKeyID:     789,
		UserDataTmpl: "",
		MaxSize:      2,
	}
	s := provider.New(cfg, fc)

	// Increase by 2
	resp, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 2,
	})

	if err != nil {
		t.Fatalf("NodeGroupIncreaseSize error: %v", err)
	}

	if resp == nil {
		t.Fatalf("want non-nil response on success")
	}

	// Verify 2 creates happened
	if fc.createCalls != 2 {
		t.Fatalf("want 2 create calls, got %d", fc.createCalls)
	}

	// Verify instance names are correct
	if len(fc.instances) != 2 {
		t.Fatalf("want 2 instances, got %d", len(fc.instances))
	}

	if fc.instances[0].Name != "fuzeinfra-elastic-0" {
		t.Fatalf("want instance[0].Name=fuzeinfra-elastic-0, got %q", fc.instances[0].Name)
	}

	if fc.instances[1].Name != "fuzeinfra-elastic-1" {
		t.Fatalf("want instance[1].Name=fuzeinfra-elastic-1, got %q", fc.instances[1].Name)
	}
}

func TestIncreaseSize_InvalidDelta(t *testing.T) {
	fc := &fakeCloudCapTest{instances: []contabo.Instance{}}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
		NamePrefix: "fuzeinfra-elastic",
		MaxSize:    10,
	}
	s := provider.New(cfg, fc)

	// Test Delta=0
	_, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 0,
	})
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("want InvalidArgument for Delta=0, got %v", err)
	}

	// Test Delta<0
	_, err = s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: -1,
	})
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("want InvalidArgument for Delta=-1, got %v", err)
	}

	// Must not create anything
	if fc.createCalls != 0 {
		t.Fatalf("must not create for invalid delta, got %d calls", fc.createCalls)
	}
}

func TestIncreaseSize_ListErrorPropagates(t *testing.T) {
	fc := &fakeCloudWithError{listErr: errors.New("transient API error")}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
		NamePrefix: "fuzeinfra-elastic",
		MaxSize:    10,
	}
	s := provider.New(cfg, fc)

	_, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 1,
	})

	if err == nil {
		t.Fatalf("want error from ListByTag, got nil")
	}
}

func TestIncreaseSize_NamingContinuesFromExisting(t *testing.T) {
	// Pre-load with instances named 0,1,2 and verify next index is 3
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 10, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}},
			{ID: 11, Name: "fuzeinfra-elastic-1", Tags: []string{"fuzeinfra-elastic"}},
			{ID: 12, Name: "fuzeinfra-elastic-2", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag:   "fuzeinfra-elastic",
		NamePrefix:   "fuzeinfra-elastic",
		ProductID:    "prod-123",
		ImageID:      "img-456",
		Region:       "us-central",
		SSHKeyID:     789,
		UserDataTmpl: "",
		MaxSize:      10,
	}
	s := provider.New(cfg, fc)

	resp, err := s.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 2,
	})

	if err != nil {
		t.Fatalf("NodeGroupIncreaseSize error: %v", err)
	}

	if resp == nil {
		t.Fatalf("want non-nil response")
	}

	// Verify 2 new instances created with indices 3 and 4
	if len(fc.instances) != 5 {
		t.Fatalf("want 5 instances total, got %d", len(fc.instances))
	}

	if fc.instances[3].Name != "fuzeinfra-elastic-3" {
		t.Fatalf("want instance[3].Name=fuzeinfra-elastic-3, got %q", fc.instances[3].Name)
	}

	if fc.instances[4].Name != "fuzeinfra-elastic-4" {
		t.Fatalf("want instance[4].Name=fuzeinfra-elastic-4, got %q", fc.instances[4].Name)
	}
}

// fakeCloudCapTest extends the standard fakeCloud with a createCalls counter.
type fakeCloudCapTest struct {
	instances   []contabo.Instance
	createCalls int
	deletedIDs  []int64
}

func (f *fakeCloudCapTest) ListByTag(_ context.Context, _ string) ([]contabo.Instance, error) {
	return f.instances, nil
}

func (f *fakeCloudCapTest) Create(_ context.Context, req contabo.CreateReq) (contabo.Instance, error) {
	f.createCalls++
	inst := contabo.Instance{
		ID:   int64(len(f.instances) + 1),
		Name: req.Name,
		Tags: req.Tags,
	}
	f.instances = append(f.instances, inst)
	return inst, nil
}

func (f *fakeCloudCapTest) Delete(_ context.Context, id int64) error {
	f.deletedIDs = append(f.deletedIDs, id)
	for i, inst := range f.instances {
		if inst.ID == id {
			f.instances = append(f.instances[:i], f.instances[i+1:]...)
			return nil
		}
	}
	return nil
}

func TestDeleteNodes_DeletesElastic(t *testing.T) {
	// Pre-load fakeCloud with an elastic instance ID=42
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 42, Name: "elastic-node-42", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
	}
	s := provider.New(cfg, fc)

	// Request to delete the elastic node with ProviderID contabo://42
	_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
		Id: "elastic",
		Nodes: []*protos.ExternalGrpcNode{
			{ProviderID: "contabo://42"},
		},
	})

	if err != nil {
		t.Fatalf("NodeGroupDeleteNodes error: %v", err)
	}

	// Verify cloud.Delete(42) was called exactly once
	if len(fc.deletedIDs) != 1 {
		t.Fatalf("want 1 delete call, got %d", len(fc.deletedIDs))
	}
	if fc.deletedIDs[0] != 42 {
		t.Fatalf("want deleted ID 42, got %d", fc.deletedIDs[0])
	}
}

func TestDeleteNodes_RefusesNonElastic(t *testing.T) {
	// Pre-load fakeCloud with an elastic instance ID=42 only
	// (ID=999 is NOT in the elastic set)
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 42, Name: "elastic-node-42", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
	}
	s := provider.New(cfg, fc)

	// Request to delete a node whose ID is NOT elastic (contabo://999)
	_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
		Id: "elastic",
		Nodes: []*protos.ExternalGrpcNode{
			{ProviderID: "contabo://999"},
		},
	})

	// Must return InvalidArgument error
	if status.Code(err) != codes.InvalidArgument {
		t.Fatalf("want InvalidArgument, got %v", err)
	}

	// Must NOT call cloud.Delete at all (fail-closed guard)
	if len(fc.deletedIDs) != 0 {
		t.Fatalf("must not delete non-elastic nodes, but got %d delete calls", len(fc.deletedIDs))
	}
}
