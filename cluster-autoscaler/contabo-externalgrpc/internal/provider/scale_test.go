package provider_test

import (
	"context"
	"errors"
	"strings"
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

	// Verify instance names are correct: prefixed, and unique from each other
	// (naming is now a random suffix, not a sequential index — see
	// uniqueInstanceName in scale.go).
	if len(fc.instances) != 2 {
		t.Fatalf("want 2 instances, got %d", len(fc.instances))
	}

	assertElasticName(t, fc.instances[0].Name, "fuzeinfra-elastic")
	assertElasticName(t, fc.instances[1].Name, "fuzeinfra-elastic")

	if fc.instances[0].Name == fc.instances[1].Name {
		t.Fatalf("want unique instance names, got duplicate %q", fc.instances[0].Name)
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

func TestIncreaseSize_NamingAvoidsCollisionWithExisting(t *testing.T) {
	// Pre-load with an untracked/still-cancelling instance holding the
	// lowest-numbered legacy sequential name. A sequential-index scheme
	// would try to reuse "fuzeinfra-elastic-0" (already taken -> Contabo
	// 400s on duplicate display name); the random-suffix scheme must instead
	// produce names that are prefixed, unique from each other, and distinct
	// from every pre-existing name.
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

	if len(fc.instances) != 5 {
		t.Fatalf("want 5 instances total, got %d", len(fc.instances))
	}

	newNames := map[string]struct{}{
		fc.instances[3].Name: {},
		fc.instances[4].Name: {},
	}
	if len(newNames) != 2 {
		t.Fatalf("want 2 distinct new instance names, got %v", newNames)
	}

	preExisting := map[string]struct{}{
		"fuzeinfra-elastic-0": {},
		"fuzeinfra-elastic-1": {},
		"fuzeinfra-elastic-2": {},
	}
	for name := range newNames {
		assertElasticName(t, name, "fuzeinfra-elastic")
		if _, collides := preExisting[name]; collides {
			t.Fatalf("new instance name %q collides with a pre-existing name", name)
		}
	}
}

// assertElasticName asserts name has the given prefix followed by
// "-<8-hex-chars>" (the random suffix format from uniqueInstanceName), and
// nothing else.
func assertElasticName(t *testing.T, name, prefix string) {
	t.Helper()

	wantPrefix := prefix + "-"
	if !strings.HasPrefix(name, wantPrefix) {
		t.Fatalf("want name with prefix %q, got %q", wantPrefix, name)
	}

	suffix := strings.TrimPrefix(name, wantPrefix)
	if len(suffix) != 8 {
		t.Fatalf("want an 8-char hex suffix, got %q (len %d) in name %q", suffix, len(suffix), name)
	}
	for _, r := range suffix {
		isHex := (r >= '0' && r <= '9') || (r >= 'a' && r <= 'f')
		if !isHex {
			t.Fatalf("want a lowercase-hex suffix, got %q in name %q", suffix, name)
		}
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
	// Pre-load fakeCloud with an elastic instance named fuzeinfra-elastic-0, numeric ID=42.
	// The providerID is name-based (contabo://<name>); the fake resolves name->id via
	// ListByTag, mirroring how the real Contabo-backed NodeGroupDeleteNodes resolves it.
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 42, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
	}
	s := provider.New(cfg, fc)

	// Request to delete the elastic node with ProviderID contabo://fuzeinfra-elastic-0
	_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
		Id: "elastic",
		Nodes: []*protos.ExternalGrpcNode{
			{ProviderID: "contabo://fuzeinfra-elastic-0"},
		},
	})

	if err != nil {
		t.Fatalf("NodeGroupDeleteNodes error: %v", err)
	}

	// Verify cloud.Delete resolved the name to numeric ID 42 and was called exactly once
	if len(fc.deletedIDs) != 1 {
		t.Fatalf("want 1 delete call, got %d", len(fc.deletedIDs))
	}
	if fc.deletedIDs[0] != 42 {
		t.Fatalf("want deleted ID 42 (resolved from name fuzeinfra-elastic-0), got %d", fc.deletedIDs[0])
	}
}

func TestDeleteNodes_RefusesNonElastic(t *testing.T) {
	// Pre-load fakeCloud with an elastic instance named fuzeinfra-elastic-0 only
	// (fuzeinfra-elastic-99 is NOT in the elastic set)
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 42, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
	}
	s := provider.New(cfg, fc)

	// Request to delete a node whose name is NOT elastic (contabo://fuzeinfra-elastic-99)
	_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
		Id: "elastic",
		Nodes: []*protos.ExternalGrpcNode{
			{ProviderID: "contabo://fuzeinfra-elastic-99"},
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

func TestDeleteNodes_MalformedProviderIDRejected(t *testing.T) {
	// A ProviderID without the contabo:// prefix, or with an empty name suffix,
	// must be rejected as InvalidArgument (fail-closed guard) without deleting anything.
	fc := &fakeCloudCapTest{
		instances: []contabo.Instance{
			{ID: 42, Name: "fuzeinfra-elastic-0", Tags: []string{"fuzeinfra-elastic"}},
		},
	}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
	}
	s := provider.New(cfg, fc)

	for _, providerID := range []string{"fuzeinfra-elastic-0", "contabo://", ""} {
		_, err := s.NodeGroupDeleteNodes(context.Background(), &protos.NodeGroupDeleteNodesRequest{
			Id: "elastic",
			Nodes: []*protos.ExternalGrpcNode{
				{ProviderID: providerID},
			},
		})
		if status.Code(err) != codes.InvalidArgument {
			t.Fatalf("providerID %q: want InvalidArgument, got %v", providerID, err)
		}
	}

	if len(fc.deletedIDs) != 0 {
		t.Fatalf("must not delete on malformed ProviderID, but got %d delete calls", len(fc.deletedIDs))
	}
}
