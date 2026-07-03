package provider_test

import (
	"context"
	"errors"
	"testing"

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
	if inst1.Id != "contabo://100" {
		t.Fatalf("instance 1: want Id=contabo://100, got %q", inst1.Id)
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
	if inst2.Id != "contabo://101" {
		t.Fatalf("instance 2: want Id=contabo://101, got %q", inst2.Id)
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
