package provider

import (
	"context"
	"fmt"
	"strings"

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

// contaboProviderIDPrefix is the scheme NodeGroupNodes stamps onto every
// instance it reports (see size.go) and that cloud-init sets on every real
// node via --kubelet-arg=provider-id (see deploy/elastic-userdata.template).
const contaboProviderIDPrefix = "contabo://"

// elasticInstanceName extracts the bare Contabo display name that identifies
// an instance, from EITHER of the two identifiers Cluster Autoscaler may put
// on an ExternalGrpcNode. It is the single correlation point between a CA
// node object and a Contabo VPS, and getting it wrong wedges the whole pool
// (see the incident note on NodeGroupForNode below).
//
// CA calls NodeGroupForNode with two structurally different kinds of node:
//
//  1. A REAL registered k8s Node. Name is the bare k3s --node-name
//     ("fuzeinfra-prod-elastic-v2-c056a22a") and ProviderID is the
//     scheme-qualified "contabo://fuzeinfra-prod-elastic-v2-c056a22a".
//
//  2. A SYNTHETIC "fake node" that CA fabricates for an instance the cloud
//     provider reported (via NodeGroupNodes) but which has NO corresponding
//     k8s Node object — an unregistered/never-joined instance. CA builds it
//     from the cloudprovider.Instance alone, so BOTH its Name and its
//     ProviderID are the raw instance Id, i.e. the scheme-qualified
//     "contabo://fuzeinfra-prod-elastic-v2-adca9568". There is no bare name
//     anywhere on that object.
//
// So a name comparison that assumes the bare form silently fails for case 2
// while looking perfectly correct for case 1. Strip the scheme from both
// fields and prefer ProviderID, which is the identifier CA guarantees is
// populated in both cases.
//
// Returns "" when neither field yields a usable name.
func elasticInstanceName(node *protos.ExternalGrpcNode) string {
	if node == nil {
		return ""
	}
	// ProviderID first: it is the identifier CA populates for both real and
	// synthetic nodes, and it is what NodeGroupDeleteNodes will be handed.
	for _, candidate := range []string{node.ProviderID, node.Name} {
		name := strings.TrimPrefix(strings.TrimSpace(candidate), contaboProviderIDPrefix)
		if name != "" {
			return name
		}
	}
	return ""
}

// NodeGroupForNode returns the node group for the given node.
// If the node resolves to an instance in the provider's elastic name
// namespace, it belongs to the "elastic" group. Name is authoritative here:
// Contabo tag assignment is eventually consistent, and a just-created worker
// must not be reported as unregistered merely because its tag has not
// propagated yet.
//
// INCIDENT (2026-09-02) — why the scheme-stripping in elasticInstanceName is
// load-bearing, and not cosmetic:
//
// This method previously compared inst.Name (bare, e.g.
// "fuzeinfra-prod-elastic-v2-adca9568") directly against req.Node.Name. For a
// real registered node that matches. For CA's synthetic fake node — the ONLY
// kind CA ever passes for an instance that never joined the cluster —
// req.Node.Name is "contabo://fuzeinfra-prod-elastic-v2-adca9568", so the
// comparison could never match and this method returned an empty NodeGroup.
//
// That single mismatch is a self-sustaining wedge, because CA's garbage
// collector for never-joined instances (removeOldUnregisteredNodes) refuses
// to act on any node it cannot attribute to a node group:
//
//	W static_autoscaler.go:759] No node group for node contabo://fuzeinfra-prod-elastic-v2-adca9568, skipping
//	W clusterstate.go:648]      Nodegroup is nil for contabo://fuzeinfra-prod-elastic-v2-adca9568
//	I static_autoscaler.go:432] 3 unregistered nodes present
//
// So three instances that booted, were billed, and never joined could never
// be reclaimed — not by CA (no node group) and not by the reaper (its
// orphan sweep only considers instances that HAVE a NotReady k8s Node, and
// these had no Node at all). They sat in ListByNamePrefix forever, and
// because NodeGroupTargetSize counts by name prefix they permanently
// consumed 3 of the pool's 4 slots:
//
//	I orchestrator.go:444] Skipping node group elastic - max size reached
//
// with 27 ARC runner pods Pending and 86 queued workflow runs on one repo
// alone. Note this is the SECOND time this pool has been wedged with the
// signature "NodeGroupTargetSize reports N while only M nodes correlate, and
// CA skips every scale-up iteration" — see the elasticTag v1 contamination
// note in helm/fuzeinfra/values-contabo.yaml. That round was attributed to
// tag contamination and fixed by cutting over to a clean v2 tag, which did
// not touch this comparison, so the wedge recurred on the clean tag. The
// scheme mismatch, not the tag, is the durable cause.
//
// Fixing the attribution is what makes the pool SELF-HEALING rather than
// merely unblocked: once CA can map a never-joined instance to the elastic
// group, its existing --max-node-provision-time timer expires and it calls
// NodeGroupDeleteNodes itself. Deliberately, the fix does NOT hide such
// instances from NodeGroupNodes/NodeGroupTargetSize — hiding them would
// unwedge scale-up while stranding the leaked, billed VPSes permanently
// invisible to every reclaim path, which is strictly worse.
func (s *Server) NodeGroupForNode(ctx context.Context, req *protos.NodeGroupForNodeRequest) (*protos.NodeGroupForNodeResponse, error) {
	nodeName := elasticInstanceName(req.Node)
	if nodeName == "" {
		// No usable identifier; return empty group.
		return &protos.NodeGroupForNodeResponse{NodeGroup: &protos.NodeGroup{}}, nil
	}

	// Fetch all instances in the managed name namespace and check by name.
	instances, err := s.cloud.ListByNamePrefix(ctx, s.cfg.NamePrefix)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupForNode: listing elastic instances by name prefix: %w", err)
	}

	// Check if the node's resolved name matches any elastic instance name.
	for _, inst := range instances {
		if inst.Name == nodeName {
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
