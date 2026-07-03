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
		protoInst := &protos.Instance{
			Id: fmt.Sprintf("contabo://%d", inst.ID),
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
