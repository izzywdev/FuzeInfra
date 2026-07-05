package provider_test

import (
	"context"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
)

// fakeCloud is a test double implementing contabo.Client.
type fakeCloud struct {
	instances []contabo.Instance
}

func (f *fakeCloud) ListByTag(_ context.Context, _ string) ([]contabo.Instance, error) {
	return f.instances, nil
}

func (f *fakeCloud) Create(_ context.Context, req contabo.CreateReq) (contabo.Instance, error) {
	inst := contabo.Instance{
		ID:   int64(len(f.instances) + 1),
		Name: req.Name,
		Tags: req.Tags,
	}
	f.instances = append(f.instances, inst)
	return inst, nil
}

func (f *fakeCloud) Delete(_ context.Context, id int64) error {
	for i, inst := range f.instances {
		if inst.ID == id {
			f.instances = append(f.instances[:i], f.instances[i+1:]...)
			return nil
		}
	}
	return nil
}

func TestGPULabelEmpty(t *testing.T) {
	s := provider.New(provider.Config{ElasticTag: "fuzeinfra-elastic", MinSize: 0, MaxSize: 2}, &fakeCloud{})
	resp, err := s.GPULabel(context.Background(), &protos.GPULabelRequest{})
	if err != nil || resp.Label != "" {
		t.Fatalf("want empty label, got %q %v", resp.Label, err)
	}
}
