// Package reaper implements the billing-aware reaper for FuzeInfra's Contabo
// elastic node pool.
//
// Contabo's cancellation semantics only take effect at the END of an
// instance's current monthly billing period, regardless of when Delete is
// called (see internal/contabo/client.go's Delete doc comment: cancel
// schedules removal at cancelDate, the instance keeps running/billing until
// then). That makes the standard Cluster Autoscaler behavior — cordon+drain
// an idle node the moment it looks unneeded — pure waste under this pricing
// model: the node becomes unusable while FuzeInfra keeps paying for it
// through the end of the month regardless.
//
// This reaper flips the economics: it leaves every elastic node fully
// usable for its entire paid month, and only cancels an instance once BOTH
// (a) it is within a configurable window of its next monthly renewal, AND
// (b) it is currently idle (no real workload pods on its matching node).
// Cluster Autoscaler's own scale-down is disabled
// (--scale-down-enabled=false, see helm/fuzeinfra) — this reaper, intended
// to run hourly as a CronJob (see cmd/reaper), is what actually releases
// capacity.
package reaper

import (
	"context"
	"errors"
	"fmt"
	"log"
	"time"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
)

// overprovisioningPriorityClass is the PriorityClass name FuzeInfra's
// overprovisioning "balloon" pause-pod deployment uses (see
// helm/fuzeinfra/templates/autoscaler/{priorityclass,overprovisioning}.yaml).
// A node whose only non-DaemonSet, non-mirror pods are overprovisioning pods
// is still considered idle for reaping purposes — the balloon pod exists
// only to hold capacity slack and reschedules freely onto any other node
// (or simply disappears with the instance).
const overprovisioningPriorityClass = "fuzeinfra-overprovisioning"

// mirrorPodAnnotation marks a static/mirror pod (e.g. a control-plane
// component reflected from a manifest on the node itself) — never a real,
// API-server-owned evictable workload.
const mirrorPodAnnotation = "kubernetes.io/config.mirror"

// elasticPoolLabel/elasticPoolValue is the node label FuzeInfra's cloud-init
// applies to every elastic node (see internal/provider/template.go). Used
// only to log an anomaly if a by-name node match doesn't carry it — the
// authoritative match is still by name (instance.Name == node.Name), per the
// join convention the provider establishes at create time.
const (
	elasticPoolLabel = "fuzeinfra.io/pool"
	elasticPoolValue = "elastic"
)

// Config configures a Reaper run.
type Config struct {
	// ReleaseWindow is how far ahead of an instance's projected monthly
	// renewal the reaper will consider releasing it (if idle). Outside this
	// window, the instance is always kept, regardless of idle status.
	ReleaseWindow time.Duration
	// BillingPeriod is the length of Contabo's monthly billing cycle, used
	// to project the next renewal as CreatedDate + BillingPeriod. It's an
	// approximation of a real monthly calendar anchor, hence configurable.
	BillingPeriod time.Duration
	// ElasticTag is the Contabo tag identifying elastic (autoscaled)
	// instances (see internal/contabo.Client.ListByTag).
	ElasticTag string
	// EvictionTimeout bounds how long a single Run spends evicting pods from
	// one node before giving up (best-effort — eviction failure does not
	// block cancellation).
	EvictionTimeout time.Duration
}

// DefaultConfig returns the documented defaults (RELEASE_WINDOW=24h,
// BILLING_PERIOD=720h/~30d, ELASTIC_TAG=fuzeinfra-elastic).
func DefaultConfig() Config {
	return Config{
		ReleaseWindow:   24 * time.Hour,
		BillingPeriod:   720 * time.Hour,
		ElasticTag:      "fuzeinfra-elastic",
		EvictionTimeout: 60 * time.Second,
	}
}

// Reaper releases elastic Contabo instances that are within their release
// window of billing renewal AND idle.
type Reaper struct {
	Contabo contabo.Client
	K8s     kubernetes.Interface
	Cfg     Config
	// Now returns the current time; overridable in tests so decision logic
	// never depends on the real wall clock.
	Now func() time.Time
}

// New builds a Reaper with Now defaulting to time.Now.
func New(client contabo.Client, k8s kubernetes.Interface, cfg Config) *Reaper {
	return &Reaper{Contabo: client, K8s: k8s, Cfg: cfg, Now: time.Now}
}

// Decision is the outcome of evaluating a single instance.
type Decision int

const (
	// DecisionKeep means the instance must NOT be canceled this run.
	DecisionKeep Decision = iota
	// DecisionRelease means the instance is within its release window and
	// idle — safe to cordon/evict/cancel.
	DecisionRelease
)

func (d Decision) String() string {
	if d == DecisionRelease {
		return "release"
	}
	return "keep"
}

// decideByRenewal is the pure billing half of the decision: given an
// instance's CreatedDate and the current time, is it even within the
// release window of its next monthly renewal? It does NOT know about idle
// status — that requires a live pod listing and is layered on top by
// Decide/Run. A zero CreatedDate (unparsable/absent on the API response)
// always means DecisionKeep — the reaper must never guess a billing anchor.
func decideByRenewal(createdDate, now time.Time, releaseWindow, billingPeriod time.Duration) (Decision, string) {
	if createdDate.IsZero() {
		return DecisionKeep, "unknown createdDate (unparsable/absent) — skipping billing-period decision for safety"
	}
	renewal := createdDate.Add(billingPeriod)
	windowStart := renewal.Add(-releaseWindow)
	if now.Before(windowStart) {
		return DecisionKeep, fmt.Sprintf("not yet within release window (renewal=%s, window opens=%s)", renewal.Format(time.RFC3339), windowStart.Format(time.RFC3339))
	}
	return DecisionRelease, fmt.Sprintf("within release window of renewal=%s", renewal.Format(time.RFC3339))
}

// Decide is the full pure decision function: given an instance's
// CreatedDate, the current time, the configured windows, and whether its
// matching node is currently idle, decide keep vs release. It performs no
// I/O — tests exercise every branch by passing `now` and `idle` explicitly
// (see reaper_test.go), independent of any fake clock/API wiring.
func Decide(createdDate, now time.Time, releaseWindow, billingPeriod time.Duration, idle bool) (Decision, string) {
	d, reason := decideByRenewal(createdDate, now, releaseWindow, billingPeriod)
	if d != DecisionRelease {
		return d, reason
	}
	if !idle {
		return DecisionKeep, "busy near renewal, will renew"
	}
	return DecisionRelease, reason
}

// Summary totals a Run's outcome for logging/observability.
type Summary struct {
	Checked  int
	Released int
	Kept     int
	Errors   int
}

// Run executes one reaper pass: list elastic instances, decide + act on
// each, and return a summary. Every instance is handled independently — one
// instance's error does not abort the pass for the rest. Only ListByTag
// failing outright (the reaper can't even see the fleet) returns an error.
func (r *Reaper) Run(ctx context.Context) (Summary, error) {
	instances, err := r.Contabo.ListByTag(ctx, r.Cfg.ElasticTag)
	if err != nil {
		return Summary{}, fmt.Errorf("list elastic instances (tag=%q): %w", r.Cfg.ElasticTag, err)
	}

	summary := Summary{Checked: len(instances)}
	now := r.Now()

	for _, inst := range instances {
		d, reason := decideByRenewal(inst.CreatedDate, now, r.Cfg.ReleaseWindow, r.Cfg.BillingPeriod)
		if d != DecisionRelease {
			log.Printf("reaper: keeping instance %d (%s): %s", inst.ID, inst.Name, reason)
			summary.Kept++
			continue
		}

		node, err := r.K8s.CoreV1().Nodes().Get(ctx, inst.Name, metav1.GetOptions{})
		if err != nil {
			if apierrors.IsNotFound(err) {
				log.Printf("reaper: WARNING no k8s Node named %q for instance %d (never joined, or already gone) — skipping, NOT canceling (could be mid-join; orphan cleanup is a separate concern)", inst.Name, inst.ID)
			} else {
				log.Printf("reaper: WARNING failed to get node %q for instance %d: %v — skipping", inst.Name, inst.ID, err)
			}
			summary.Kept++
			continue
		}
		if !isNodeReady(node) {
			log.Printf("reaper: WARNING node %q (instance %d) is not Ready — skipping, NOT canceling (fail-safe: only a matching Ready node authorizes release)", node.Name, inst.ID)
			summary.Kept++
			continue
		}
		if v := node.Labels[elasticPoolLabel]; v != elasticPoolValue {
			log.Printf("reaper: WARNING node %q (instance %d) matched by name but is missing label %s=%s (got %q) — proceeding on the name match, but this is worth investigating", node.Name, inst.ID, elasticPoolLabel, elasticPoolValue, v)
		}

		idle, err := r.isNodeIdle(ctx, node.Name)
		if err != nil {
			log.Printf("reaper: WARNING failed to determine idle status of node %q (instance %d): %v — skipping (fail-safe: never cancel without a confirmed idle check)", node.Name, inst.ID, err)
			summary.Kept++
			continue
		}
		if !idle {
			log.Printf("reaper: keeping %s: busy near renewal, will renew", inst.Name)
			summary.Kept++
			continue
		}

		log.Printf("reaper: releasing %s (instance %d): %s, idle=true", inst.Name, inst.ID, reason)
		if err := r.release(ctx, node, inst); err != nil {
			log.Printf("reaper: ERROR releasing %s (instance %d): %v", inst.Name, inst.ID, err)
			summary.Errors++
			continue
		}
		summary.Released++
	}

	log.Printf("reaper: summary checked=%d released=%d kept=%d errors=%d", summary.Checked, summary.Released, summary.Kept, summary.Errors)
	return summary, nil
}

// isNodeReady reports whether node has condition Ready=True.
func isNodeReady(node *corev1.Node) bool {
	for _, c := range node.Status.Conditions {
		if c.Type == corev1.NodeReady {
			return c.Status == corev1.ConditionTrue
		}
	}
	return false
}

// isDaemonSetPod reports whether p is owned by a DaemonSet — present on
// every node by design, never counted against idle status and never
// evicted (deleting the node removes it; eviction would just cause an
// immediate DaemonSet-controller-driven recreate on the same node).
func isDaemonSetPod(p corev1.Pod) bool {
	for _, owner := range p.OwnerReferences {
		if owner.Kind == "DaemonSet" {
			return true
		}
	}
	return false
}

// isMirrorPod reports whether p is a static/mirror pod reflected from a
// manifest on the node itself (kubelet-owned, not API-server-owned — cannot
// be meaningfully evicted or rescheduled).
func isMirrorPod(p corev1.Pod) bool {
	_, ok := p.Annotations[mirrorPodAnnotation]
	return ok
}

// isOverprovisioningPod reports whether p is FuzeInfra's overprovisioning
// balloon pause pod (see the overprovisioningPriorityClass doc comment).
func isOverprovisioningPod(p corev1.Pod) bool {
	return p.Spec.PriorityClassName == overprovisioningPriorityClass
}

// isIdleIgnorable reports whether p should NOT count against a node's idle
// status, per the reaper's idle definition: DaemonSet-owned, static/mirror,
// or the overprovisioning balloon pod. Any other pod present means the node
// is NOT idle.
func isIdleIgnorable(p corev1.Pod) bool {
	return isDaemonSetPod(p) || isMirrorPod(p) || isOverprovisioningPod(p)
}

// isNodeIdle reports whether nodeName's only pods are ones isIdleIgnorable
// excuses (DaemonSet/mirror/overprovisioning). Any other pod means real
// workload is still scheduled there, so the node must NOT be reaped.
func (r *Reaper) isNodeIdle(ctx context.Context, nodeName string) (bool, error) {
	pods, err := r.K8s.CoreV1().Pods(metav1.NamespaceAll).List(ctx, metav1.ListOptions{
		FieldSelector: "spec.nodeName=" + nodeName,
	})
	if err != nil {
		return false, fmt.Errorf("list pods on node %q: %w", nodeName, err)
	}
	for _, p := range pods.Items {
		if !isIdleIgnorable(p) {
			return false, nil
		}
	}
	return true, nil
}

// release cordons node, best-effort evicts its remaining non-DaemonSet pods
// (respecting PDBs via the Eviction API, bounded by Cfg.EvictionTimeout),
// then cancels the matching Contabo instance. Cordon+cancel failures are
// returned as hard errors (the caller counts them and moves on to the next
// instance); eviction failures are logged and swallowed — the instance is
// being canceled either way, so a stuck eviction shouldn't block release, it
// just means a leftover pod gets hard-terminated with the node instead of
// gracefully evicted first.
func (r *Reaper) release(ctx context.Context, node *corev1.Node, inst contabo.Instance) error {
	if err := r.cordon(ctx, node); err != nil {
		return fmt.Errorf("cordon node %q: %w", node.Name, err)
	}
	log.Printf("reaper: cordoned node %q", node.Name)

	if err := r.evictAll(ctx, node.Name); err != nil {
		log.Printf("reaper: WARNING eviction on node %q did not fully complete: %v (continuing to cancel the instance anyway)", node.Name, err)
	} else {
		log.Printf("reaper: evicted all evictable pods from node %q", node.Name)
	}

	if err := r.Contabo.Delete(ctx, inst.ID); err != nil {
		return fmt.Errorf("cancel contabo instance %d: %w", inst.ID, err)
	}
	log.Printf("reaper: canceled contabo instance %d (%s) — terminates at end of billing period, will NOT renew", inst.ID, inst.Name)
	return nil
}

// cordon sets node.Spec.Unschedulable=true via a merge patch, no-op if
// already cordoned.
func (r *Reaper) cordon(ctx context.Context, node *corev1.Node) error {
	if node.Spec.Unschedulable {
		return nil
	}
	patch := []byte(`{"spec":{"unschedulable":true}}`)
	_, err := r.K8s.CoreV1().Nodes().Patch(ctx, node.Name, types.MergePatchType, patch, metav1.PatchOptions{})
	return err
}

// evictAll evicts every non-DaemonSet pod remaining on nodeName via the
// Eviction API (POST pods/{name}/eviction), which honors PodDisruptionBudgets
// server-side. Bounded by Cfg.EvictionTimeout. Best-effort: all failures are
// collected and returned joined, but every pod is still attempted.
func (r *Reaper) evictAll(ctx context.Context, nodeName string) error {
	timeout := r.Cfg.EvictionTimeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	evictCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	pods, err := r.K8s.CoreV1().Pods(metav1.NamespaceAll).List(evictCtx, metav1.ListOptions{
		FieldSelector: "spec.nodeName=" + nodeName,
	})
	if err != nil {
		return fmt.Errorf("list pods on node %q: %w", nodeName, err)
	}

	var errs []error
	for _, p := range pods.Items {
		if isDaemonSetPod(p) {
			continue
		}
		eviction := &policyv1.Eviction{
			ObjectMeta: metav1.ObjectMeta{
				Name:      p.Name,
				Namespace: p.Namespace,
			},
		}
		if err := r.K8s.PolicyV1().Evictions(p.Namespace).Evict(evictCtx, eviction); err != nil && !apierrors.IsNotFound(err) {
			errs = append(errs, fmt.Errorf("evict %s/%s: %w", p.Namespace, p.Name, err))
		}
	}
	if len(errs) > 0 {
		return errors.Join(errs...)
	}
	return nil
}
