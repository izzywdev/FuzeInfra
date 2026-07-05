package provider

import (
	"context"
	"fmt"
	"strings"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
)

// NodeGroupTargetSize returns the current target size of the node group,
// which is the number of elastic Contabo instances.
func (s *Server) NodeGroupTargetSize(ctx context.Context, req *protos.NodeGroupTargetSizeRequest) (*protos.NodeGroupTargetSizeResponse, error) {
	instances, err := s.cloud.ListByTag(ctx, s.cfg.ElasticTag)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupTargetSize: listing elastic instances: %w", err)
	}

	return &protos.NodeGroupTargetSizeResponse{
		TargetSize: int32(len(instances)),
	}, nil
}

// NodeGroupNodes returns the list of nodes in the node group,
// mapped from elastic Contabo instances to the proto Instance format.
func (s *Server) NodeGroupNodes(ctx context.Context, req *protos.NodeGroupNodesRequest) (*protos.NodeGroupNodesResponse, error) {
	instances, err := s.cloud.ListByTag(ctx, s.cfg.ElasticTag)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupNodes: listing elastic instances: %w", err)
	}

	protoInstances := make([]*protos.Instance, 0, len(instances))
	for _, inst := range instances {
		state := mapContaboStatusToProtoState(inst.Status)
		// The providerID is name-based (contabo://<name>), NOT the numeric Contabo
		// instance ID. This is required because CA correlates a k8s Node to a
		// cloud instance via Node.Spec.ProviderID, which is set at node-join time
		// via the --kubelet-arg=provider-id=contabo://<node-name> flag in cloud-init
		// (see deploy/elastic-userdata.template). The numeric Contabo id is not
		// known to the node at join time (it's assigned by the Contabo API before
		// the node boots, but nothing threads it into the kubelet flags), so using
		// it here would mean no real k8s node ever has a matching ProviderID and
		// scale-down (NodeGroupDeleteNodes) could never resolve which node to
		// delete. The name IS known at render time on both sides, so it's the only
		// value that reliably correlates a k8s Node object with its Contabo VPS.
		protoInst := &protos.Instance{
			Id: "contabo://" + inst.Name,
			Status: &protos.InstanceStatus{
				InstanceState: state,
				ErrorInfo:     nil,
			},
		}
		protoInstances = append(protoInstances, protoInst)
	}

	return &protos.NodeGroupNodesResponse{
		Instances: protoInstances,
	}, nil
}

// mapContaboStatusToProtoState maps Contabo instance status strings
// to the corresponding proto InstanceStatus_InstanceState enum values.
func mapContaboStatusToProtoState(contaboStatus string) protos.InstanceStatus_InstanceState {
	status := strings.ToLower(strings.TrimSpace(contaboStatus))
	switch status {
	// Creating states
	case "provisioning", "installing", "pending":
		return protos.InstanceStatus_instanceCreating
	// Running states
	case "running", "ready":
		return protos.InstanceStatus_instanceRunning
	// Deleting states
	case "deleting", "deleted", "stopping":
		return protos.InstanceStatus_instanceDeleting
	// Default to unspecified
	default:
		return protos.InstanceStatus_unspecified
	}
}
