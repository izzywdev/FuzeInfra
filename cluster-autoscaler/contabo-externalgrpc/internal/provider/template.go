package provider

import (
	"context"
	"fmt"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// productSpec holds the hardware specification for a Contabo product SKU.
// vCPU and MemGi values are based on Contabo's published catalog at
// https://contabo.com/en/vps/ — verify against the current catalog if these
// are used for scheduling-critical capacity decisions.
type productSpec struct {
	VCPU  int
	MemGi int
}

// productSpecs maps Contabo VPS/VDS product IDs to their hardware capacities.
// Whitelisted SKUs for elastic node groups: V1, V45, V46, V47, V76, V92.
//
// IMPORTANT: Specs marked "best-effort" should be verified against Contabo's
// current catalog before use in production. Wrong capacity causes CA to make
// incorrect scale-up decisions.
var productSpecs = map[string]productSpec{
	// VPS product line
	"V1":  {VCPU: 4, MemGi: 8},   // VPS S — best-effort; verify against catalog
	"V45": {VCPU: 6, MemGi: 16},  // VPS M — verified as primary elastic SKU
	"V46": {VCPU: 8, MemGi: 24},  // VPS L — best-effort; verify against catalog
	"V47": {VCPU: 10, MemGi: 32}, // VPS XL — best-effort; verify against catalog
	// VDS product line
	"V76": {VCPU: 6, MemGi: 16},  // VDS S — best-effort; verify against catalog
	"V92": {VCPU: 12, MemGi: 48}, // VDS M — best-effort; verify against catalog
}

// NodeGroupTemplateNodeInfo returns a synthetic Node describing the capacity,
// labels, and taints that a would-be elastic Contabo node will have at join time.
//
// This is called by Cluster Autoscaler when min=0 elastic nodes exist and CA
// needs to determine whether a pending pod would fit on a *future* elastic node.
// The template MUST mirror exactly what the real node gets from cloud-init:
//   - label fuzeinfra.io/pool=elastic
//   - taint fuzeinfra.io/elastic=true:PreferNoSchedule
//
// If the labels or taints diverge from what cloud-init sets, CA's scheduling
// simulation will be incorrect, leading to wrong scale-up (or no scale-up) decisions.
func (s *Server) NodeGroupTemplateNodeInfo(ctx context.Context, req *protos.NodeGroupTemplateNodeInfoRequest) (*protos.NodeGroupTemplateNodeInfoResponse, error) {
	spec, ok := productSpecs[s.cfg.ProductID]
	if !ok {
		return nil, status.Errorf(codes.Internal, "no product spec for %s — add it to productSpecs in template.go", s.cfg.ProductID)
	}

	nodeName := fmt.Sprintf("%s-template", s.cfg.NamePrefix)

	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name: nodeName,
			// Labels must exactly match what the real elastic node receives at k3s agent join time.
			// Task 14 (cloud-init) applies the same labels; keep them in sync.
			Labels: map[string]string{
				"fuzeinfra.io/pool":      "elastic",
				"kubernetes.io/hostname": nodeName,
				"kubernetes.io/os":       "linux",
				"kubernetes.io/arch":     "amd64",
			},
		},
		Spec: corev1.NodeSpec{
			// Taints must exactly match what cloud-init applies at k3s agent join time.
			// PreferNoSchedule means pods without a toleration prefer to avoid this node,
			// while pods that explicitly tolerate it (or have no preference) can land here.
			Taints: []corev1.Taint{
				{
					Key:    "fuzeinfra.io/elastic",
					Value:  "true",
					Effect: corev1.TaintEffectPreferNoSchedule,
				},
			},
		},
		Status: corev1.NodeStatus{
			Capacity: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(fmt.Sprintf("%d", spec.VCPU)),
				corev1.ResourceMemory: resource.MustParse(fmt.Sprintf("%dGi", spec.MemGi)),
				corev1.ResourcePods:   resource.MustParse("110"),
			},
			// Allocatable equals Capacity for the template — CA uses this for scheduling simulation.
			// A real node has slightly less allocatable due to system reserved resources, but for
			// scale-from-zero templates the standard practice is to use full capacity.
			Allocatable: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse(fmt.Sprintf("%d", spec.VCPU)),
				corev1.ResourceMemory: resource.MustParse(fmt.Sprintf("%dGi", spec.MemGi)),
				corev1.ResourcePods:   resource.MustParse("110"),
			},
			Conditions: []corev1.NodeCondition{
				{
					Type:   corev1.NodeReady,
					Status: corev1.ConditionTrue,
				},
			},
		},
	}

	return &protos.NodeGroupTemplateNodeInfoResponse{
		NodeInfo: node,
	}, nil
}
