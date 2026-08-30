package provider

import (
	"context"
	"fmt"

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
// If the node name matches the provider's elastic name prefix, it belongs to
// the "elastic" group.  Name is authoritative here: Contabo tag assignment
// is eventually consistent, and a just-created worker must not be reported as
// unregistered merely because its tag has not propagated yet.
func (s *Server) NodeGroupForNode(ctx context.Context, req *protos.NodeGroupForNodeRequest) (*protos.NodeGroupForNodeResponse, error) {
	if req.Node == nil {
		// No node provided; return empty group
		return &protos.NodeGroupForNodeResponse{NodeGroup: &protos.NodeGroup{}}, nil
	}

	// Fetch all instances in the managed name namespace and check by name.
	instances, err := s.cloud.ListByNamePrefix(ctx, s.cfg.NamePrefix)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupForNode: listing elastic instances by name prefix: %w", err)
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
