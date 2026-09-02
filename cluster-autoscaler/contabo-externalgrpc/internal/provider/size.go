package provider

import (
	"context"
	"fmt"
	"strings"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
)

// liveElasticInstances filters out instances that Contabo has already been
// told to remove, returning only those that can still legitimately hold a
// slot in the elastic pool.
//
// This is load-bearing for convergence, and it is a direct consequence of
// Contabo's cancellation semantics (see internal/contabo.Client.Delete):
// there is NO immediate-terminate API. POST /v1/compute/instances/{id}/cancel
// only SCHEDULES removal at the end of the current billing period, and until
// that date the instance keeps running, keeps being returned by
// GET /v1/compute/instances, and therefore keeps matching ListByNamePrefix —
// for up to a month.
//
// Without this filter the pool cannot recover from its own scale-down. The
// moment CA reclaims a node, that node's instance comes back cancelled-but-
// listed, NodeGroupTargetSize keeps counting it, and the group sits pinned at
// MaxSize emitting "Skipping node group elastic - max size reached" until the
// billing period rolls over. That is the same wedge the NodeGroupForNode fix
// addresses, just re-entered through the delete path — so fixing only the
// attribution bug would have unwedged the pool exactly once and then
// re-wedged it on the first reclaim. Delete's doc comment flagged this as a
// follow-up; making CA able to reclaim nodes at all is what promotes it from
// theoretical to load-bearing.
//
// SPEND TRADE-OFF, deliberate: excluding a cancelled instance frees its slot
// immediately, so the pool may briefly run MaxSize live instances alongside
// cancelled-but-not-yet-terminated ones. That is bounded and is exactly the
// billing-aware design's intent — the reaper only cancels within 24h of
// renewal precisely so replacement capacity can come up before the old
// instance's paid month ends. The alternative (counting cancelled instances)
// trades a bounded ~24h overlap for a pool that cannot scale up for up to a
// month, which is what just starved CI fleet-wide.
//
// This does NOT reopen the untagged-orphan runaway hole that ListByNamePrefix
// exists to close (see NodeGroupIncreaseSize): an instance only acquires a
// CancelDate because we explicitly cancelled it. An orphan we never cancelled
// still counts, still binds the cap, and is still reported to CA so it can be
// reclaimed rather than silently stranded.
//
// The filter keys on CancelDate ONLY, deliberately not on Status. Contabo's
// transient statuses ("stopping", "deleting") are mapped to
// InstanceStatus_instanceDeleting and reported to CA on purpose, so CA can
// run its own state machine over them — TestNodeGroupNodes_StateMapping pins
// that contract. CancelDate is the unambiguous, explicit signal that WE asked
// for this instance to go away; a status string is a transient observation
// and a "stopping" instance may well come back.
func liveElasticInstances(instances []contabo.Instance) []contabo.Instance {
	live := make([]contabo.Instance, 0, len(instances))
	for _, inst := range instances {
		// Already scheduled for termination by Contabo; nothing further for
		// CA or this provider to do, and it must not hold a slot.
		if !inst.CancelDate.IsZero() {
			continue
		}
		live = append(live, inst)
	}
	return live
}

// NodeGroupTargetSize returns the current target size of the node group,
// which is the number of Contabo instances in the managed name namespace.
// Name-prefix membership is authoritative because tag assignment is
// eventually consistent.
func (s *Server) NodeGroupTargetSize(ctx context.Context, req *protos.NodeGroupTargetSizeRequest) (*protos.NodeGroupTargetSizeResponse, error) {
	instances, err := s.cloud.ListByNamePrefix(ctx, s.cfg.NamePrefix)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupTargetSize: listing elastic instances by name prefix: %w", err)
	}

	s.mu.Lock()
	reserved := s.inFlight
	s.mu.Unlock()

	// Cancelled instances must not hold a slot — see liveElasticInstances.
	live := liveElasticInstances(instances)

	return &protos.NodeGroupTargetSizeResponse{
		TargetSize: int32(len(live) + reserved),
	}, nil
}

// NodeGroupNodes returns the list of nodes in the node group,
// mapped from elastic Contabo instances to the proto Instance format.
func (s *Server) NodeGroupNodes(ctx context.Context, req *protos.NodeGroupNodesRequest) (*protos.NodeGroupNodesResponse, error) {
	instances, err := s.cloud.ListByNamePrefix(ctx, s.cfg.NamePrefix)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupNodes: listing elastic instances by name prefix: %w", err)
	}

	// Report only instances that still hold a slot. A cancelled instance is
	// already on Contabo's termination path; continuing to report it would
	// pin the group at MaxSize for the rest of the billing period (see
	// liveElasticInstances). Never-joined instances we did NOT cancel are
	// deliberately still reported, so CA can attribute and reclaim them
	// rather than have them silently stranded.
	instances = liveElasticInstances(instances)

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
