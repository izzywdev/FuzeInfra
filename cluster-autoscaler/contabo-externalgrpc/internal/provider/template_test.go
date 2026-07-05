package provider_test

import (
	"context"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
)

func TestNodeGroupTemplateNodeInfo_V45_Capacity(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "V45",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo error: %v", err)
	}
	if resp.NodeInfo == nil {
		t.Fatal("expected non-nil NodeInfo")
	}

	node := resp.NodeInfo

	// Check CPU capacity: V45 = 6 vCPU
	cpu := node.Status.Capacity[corev1.ResourceCPU]
	if cpu.Value() != 6 {
		t.Errorf("want CPU=6, got %d", cpu.Value())
	}

	// Check memory capacity: V45 = 16Gi
	const wantMemBytes = int64(16 * 1024 * 1024 * 1024)
	mem := node.Status.Capacity[corev1.ResourceMemory]
	if mem.Value() != wantMemBytes {
		t.Errorf("want Memory=%d (16Gi), got %d", wantMemBytes, mem.Value())
	}

	// Allocatable must match Capacity for the template
	cpuAlloc := node.Status.Allocatable[corev1.ResourceCPU]
	if cpuAlloc.Value() != 6 {
		t.Errorf("want Allocatable CPU=6, got %d", cpuAlloc.Value())
	}

	memAlloc := node.Status.Allocatable[corev1.ResourceMemory]
	if memAlloc.Value() != wantMemBytes {
		t.Errorf("want Allocatable Memory=%d (16Gi), got %d", wantMemBytes, memAlloc.Value())
	}
}

func TestNodeGroupTemplateNodeInfo_V45_Labels(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "V45",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo error: %v", err)
	}

	node := resp.NodeInfo

	// Mandatory pool label — must match what the real elastic node gets at join time.
	if v, ok := node.Labels["fuzeinfra.io/pool"]; !ok || v != "elastic" {
		t.Errorf("want label fuzeinfra.io/pool=elastic, got %q (present=%v)", v, ok)
	}

	// Standard k8s labels
	if v, ok := node.Labels["kubernetes.io/os"]; !ok || v != "linux" {
		t.Errorf("want label kubernetes.io/os=linux, got %q (present=%v)", v, ok)
	}
	if v, ok := node.Labels["kubernetes.io/arch"]; !ok || v != "amd64" {
		t.Errorf("want label kubernetes.io/arch=amd64, got %q (present=%v)", v, ok)
	}

	// hostname label must be set (to the template node name)
	if _, ok := node.Labels["kubernetes.io/hostname"]; !ok {
		t.Error("want label kubernetes.io/hostname set")
	}
}

func TestNodeGroupTemplateNodeInfo_V45_Taint(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "V45",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo error: %v", err)
	}

	node := resp.NodeInfo

	// Must have the elastic taint matching what cloud-init applies at join time.
	found := false
	for _, taint := range node.Spec.Taints {
		if taint.Key == "fuzeinfra.io/elastic" && taint.Value == "true" && taint.Effect == corev1.TaintEffectPreferNoSchedule {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("want taint fuzeinfra.io/elastic=true:PreferNoSchedule; got taints: %v", node.Spec.Taints)
	}
}

func TestNodeGroupTemplateNodeInfo_ReadyCondition(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "V45",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo error: %v", err)
	}

	node := resp.NodeInfo

	found := false
	for _, cond := range node.Status.Conditions {
		if cond.Type == corev1.NodeReady && cond.Status == corev1.ConditionTrue {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("want Ready=True condition; got conditions: %v", node.Status.Conditions)
	}
}

func TestNodeGroupTemplateNodeInfo_UnknownProduct_ReturnsInternal(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "UNKNOWN99",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	_, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err == nil {
		t.Fatal("want error for unknown product, got nil")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("want gRPC status error, got %T: %v", err, err)
	}
	if st.Code() != codes.Internal {
		t.Errorf("want codes.Internal, got %v", st.Code())
	}
}

func TestNodeGroupTemplateNodeInfo_NodeName(t *testing.T) {
	cfg := provider.Config{
		NamePrefix: "myprefix",
		ProductID:  "V45",
		MinSize:    0,
		MaxSize:    2,
	}
	s := provider.New(cfg, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo error: %v", err)
	}

	node := resp.NodeInfo
	expectedName := "myprefix-template"
	if node.Name != expectedName {
		t.Errorf("want node name %q, got %q", expectedName, node.Name)
	}

	// hostname label must match node name
	if v := node.Labels["kubernetes.io/hostname"]; v != expectedName {
		t.Errorf("want kubernetes.io/hostname=%q, got %q", expectedName, v)
	}
}
