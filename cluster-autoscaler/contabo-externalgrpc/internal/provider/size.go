package provider

import (
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
)

// instanceDiag rate-limits the per-instance diagnostic below. CA polls this
// RPC about once a second, so an unthrottled line per instance per call would
// be thousands of lines an hour.
var (
	instanceDiagMu   sync.Mutex
	instanceDiagLast time.Time
)

// logInstanceDiag prints, at most once a minute, what the Contabo list
// endpoint actually returned for each elastic instance: name, status, and the
// RAW cancelDate string alongside whether it parsed.
//
// This exists because a merged fix (#832, date-only cancelDate parsing) did
// not change the observed behaviour: CA still reported the same instances as
// longUnregistered and the provider still re-issued cancel against them every
// ~35s, while the log contained ZERO "unparsable cancelDate" lines. Those two
// facts together mean the parsed value was zero WITHOUT the field ever being
// non-empty -- but that was an inference, and cancelling paid servers on an
// inference is how three of them were cancelled in the first place. This makes
// the actual value observable so the fix can be made on evidence.
//
// Names, statuses and dates only -- never a token or credential.
func logInstanceDiag(instances []contabo.Instance) {
	instanceDiagMu.Lock()
	defer instanceDiagMu.Unlock()
	if time.Since(instanceDiagLast) < time.Minute {
		return
	}
	instanceDiagLast = time.Now()
	for _, inst := range instances {
		log.Printf("contabo-diag: instance %d name=%q status=%q rawCancelDate=%q parsedCancelDateZero=%t",
			inst.ID, inst.Name, inst.Status, inst.RawCancelDate, inst.CancelDate.IsZero())
	}
}

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

// NodeGroupNodes/NodeGroupTargetSize deliberately apply NO filter at all --
// see the two doc comments below for why. This file makes a cap/report
// split, not a cap/report DUPLICATE: liveElasticInstances (above) exists so
// scale-up always has room to create a fresh replacement, and is used ONLY
// for that cap, in NodeGroupIncreaseSize. It must NOT also be applied here.
//
// A cancelled instance is not a liability the moment it is cancelled:
// Contabo's cancellation is end-of-billing-period only (see Delete's doc
// comment), so a cancelled instance keeps running, fully paid for, until
// that date. Treating it as gone the instant it is cancelled -- which is
// what happened when liveElasticInstances was ALSO used here -- meant a
// caller with three cancelled, still-running, still-billed instances had
// zero schedulable capacity from any of them: paying for compute that k3s
// was never told still existed, and unable to create a replacement either
// (that's what the cap-side fix addresses) -- stuck either way.
//
// A second, narrower filter keyed on Contabo's Status ("deleting"/"deleted")
// was considered and rejected: it would contradict the existing, tested
// contract that Status drives ONLY state mapping here, never exclusion --
// see mapContaboStatusToProtoState and TestNodeGroupNodes_StateMapping, which
// deliberately reports "deleting"/"stopping"/"deleted" instances (mapped to
// instanceDeleting) so CA can run its own state machine over them, on the
// grounds that a transient status string may still revert. CancelDate is the
// only unambiguous, explicit "we asked for this to go away" signal; Status
// never gates exclusion here, full stop.
//
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

	// Must match what NodeGroupNodes reports, or CA's own bookkeeping sees a
	// target size that disagrees with the node list it was just handed --
	// see the doc comment above for why no filter is applied here.
	return &protos.NodeGroupTargetSizeResponse{
		TargetSize: int32(len(instances) + reserved),
	}, nil
}

// NodeGroupNodes returns the list of nodes in the node group,
// mapped from elastic Contabo instances to the proto Instance format.
func (s *Server) NodeGroupNodes(ctx context.Context, req *protos.NodeGroupNodesRequest) (*protos.NodeGroupNodesResponse, error) {
	instances, err := s.cloud.ListByNamePrefix(ctx, s.cfg.NamePrefix)
	if err != nil {
		return nil, fmt.Errorf("NodeGroupNodes: listing elastic instances by name prefix: %w", err)
	}

	// Report every instance ListByNamePrefix returns, cancelled or not -- see
	// the doc comment above NodeGroupTargetSize for why no filter is applied
	// here. A cancelled instance stays fully paid-for and fully usable until
	// Contabo actually tears it down; not reporting it here was compute
	// already being paid for and never handed to k3s. Never-joined instances
	// we did NOT cancel are still reported regardless, so CA can attribute
	// and reclaim them rather than have them silently stranded.
	logInstanceDiag(instances)

	protoInstances := make([]*protos.Instance, 0, len(instances))
	for _, inst := range instances {
		state := mapContaboStatusToProtoState(inst.Status)

		// GUARD 1 (exempt pending_payment from the unregistered-cancel
		// ratchet). Upstream clusterstate.go's expectedToRegister() drops an
		// instance from the "unregistered" set ONLY when its state is
		// instanceDeleting OR its ErrorInfo is non-nil. A Contabo order stuck in
		// pending_payment has NOT had its cloud-init applied and cannot join
		// until payment clears, yet removeOldUnregisteredNodes gates purely on
		// elapsed time — so reported as a plain instanceCreating it hits
		// maxNodeProvisionTime, is declared longUnregistered, and gets cancelled
		// (destroying a VPS the instant payment makes it real) while CA orders a
		// replacement. Pairing it with a non-nil ErrorInfo makes CA hold it as
		// an expected-but-erroring instance instead of ratcheting. See
		// helm/fuzeinfra/values-contabo.yaml clusterAutoscaler re-enable checklist #1.
		var errorInfo *protos.InstanceErrorInfo
		if isPendingPayment(inst.Status) {
			errorInfo = &protos.InstanceErrorInfo{
				ErrorCode:    "pending_payment",
				ErrorMessage: "Contabo order awaiting payment; cloud-init not applied, cannot join until payment clears",
			}
		}
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
				ErrorInfo:     errorInfo,
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
	// Creating states. pending_payment is an ordered-but-unpaid instance:
	// still "coming up" from CA's perspective, and paired with a non-nil
	// ErrorInfo in NodeGroupNodes (Guard 1) so CA exempts it from the
	// unregistered-timeout cancel path rather than ratcheting on it.
	case "provisioning", "installing", "pending", "pending_payment", "pendingpayment":
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

// isPendingPayment reports whether a Contabo instance status means the order
// has been placed but not yet paid. Contabo does NOT apply an instance's
// cloud-init userData until payment clears, so such an instance can never join
// k3s while in this state — and once payment clears it becomes a paid, running
// VPS that must never be cancelled (see NodeGroupDeleteNodes Guard 3). Matched
// tolerantly (case- and separator-insensitive) because Contabo has been seen to
// return both "pending_payment" and "pendingPayment".
func isPendingPayment(contaboStatus string) bool {
	s := strings.ToLower(strings.TrimSpace(contaboStatus))
	s = strings.NewReplacer("_", "", "-", "", " ", "").Replace(s)
	return s == "pendingpayment"
}

// pendingPaymentInstances returns the subset of instances whose status is
// pending_payment (an unpaid, unregisterable outstanding order).
func pendingPaymentInstances(instances []contabo.Instance) []contabo.Instance {
	var out []contabo.Instance
	for _, inst := range instances {
		if isPendingPayment(inst.Status) {
			out = append(out, inst)
		}
	}
	return out
}

// instanceNames returns the display names of the given instances, for logging.
func instanceNames(instances []contabo.Instance) []string {
	names := make([]string, 0, len(instances))
	for _, inst := range instances {
		names = append(names, inst.Name)
	}
	return names
}
