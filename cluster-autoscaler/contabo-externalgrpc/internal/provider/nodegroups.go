package provider

import (
	"context"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
)

// NodeGroups returns all node groups the cloud provider supports.
// For Contabo/Fuze, we have a single "elastic" group that scales elastically.
func (s *Server) NodeGroups(ctx context.Context, _ *protos.NodeGroupsRequest) (*protos.NodeGroupsResponse, error) {
	return &protos.NodeGroupsResponse{
		NodeGroups: []*protos.NodeGroup{
			{
				Id:      "elastic",
				MinSize: int32(s.cfg.MinSize),
				MaxSize: int32(s.cfg.MaxSize),
			},
		},
	}, nil
}

// NodeGroupForNode returns the node group for the given node.
// If the node is tagged with ElasticTag, it belongs to the "elastic" group.
// Otherwise it is a baseline/control-plane node and returns an empty node group (id = "").
func (s *Server) NodeGroupForNode(ctx context.Context, req *protos.NodeGroupForNodeRequest) (*protos.NodeGroupForNodeResponse, error) {
	if req.Node == nil {
		// No node provided; return empty group
		return &protos.NodeGroupForNodeResponse{NodeGroup: &protos.NodeGroup{}}, nil
	}

	// Fetch elastic instances and check if the node matches any of them by name.
	instances, err := s.cloud.ListByTag(ctx, s.cfg.ElasticTag)
	if err != nil {
		// On error, return an empty group (node is not recognized as elastic).
		return &protos.NodeGroupForNodeResponse{NodeGroup: &protos.NodeGroup{}}, nil
	}

	// Check if the node's name matches any elastic instance name.
	for _, inst := range instances {
		if inst.Name == req.Node.Name {
			// Found a match; the node belongs to the elastic group.
			return &protos.NodeGroupForNodeResponse{
				NodeGroup: &protos.NodeGroup{
					Id:      "elastic",
					MinSize: int32(s.cfg.MinSize),
					MaxSize: int32(s.cfg.MaxSize),
				},
			}, nil
		}
	}

	// No match; return an empty node group (node is not elastic-managed).
	return &protos.NodeGroupForNodeResponse{NodeGroup: &protos.NodeGroup{}}, nil
}
