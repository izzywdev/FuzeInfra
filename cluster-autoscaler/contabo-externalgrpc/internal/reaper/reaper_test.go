package reaper

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	k8sfake "k8s.io/client-go/kubernetes/fake"
)

const (
	testReleaseWindow = 24 * time.Hour
	testBillingPeriod = 720 * time.Hour // 30d
)

// --- Pure decision-function tests (no clock, no I/O) -----------------------

func TestDecide_UnknownCreatedDateAlwaysKeeps(t *testing.T) {
	now := time.Date(2026, 7, 22, 12, 0, 0, 0, time.UTC)
	d, reason := Decide(time.Time{}, now, testReleaseWindow, testBillingPeriod, true /* idle */)
	if d != DecisionKeep {
		t.Fatalf("Decide() = %v, want DecisionKeep for a zero/unknown CreatedDate", d)
	}
	if reason == "" {
		t.Fatal("expected a non-empty reason")
	}
}

func TestDecide_WellWithinBillingPeriodKeepsRegardlessOfIdle(t *testing.T) {
	// Created 1 day ago; 30d billing period means renewal is ~29 days out —
	// nowhere near the 24h release window. Must keep even if idle.
	created := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC)
	now := created.Add(24 * time.Hour)
	d, _ := Decide(created, now, testReleaseWindow, testBillingPeriod, true)
	if d != DecisionKeep {
		t.Fatalf("Decide() = %v, want DecisionKeep well before the release window opens", d)
	}
}

func TestDecide_InWindowButBusyKeeps(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour) // 1h before renewal, inside the 24h window
	d, reason := Decide(created, now, testReleaseWindow, testBillingPeriod, false /* idle */)
	if d != DecisionKeep {
		t.Fatalf("Decide() = %v, want DecisionKeep for a busy node inside the release window", d)
	}
	if reason != "busy near renewal, will renew" {
		t.Fatalf("reason = %q, want the busy-near-renewal message", reason)
	}
}

func TestDecide_InWindowAndIdleReleases(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour)
	d, _ := Decide(created, now, testReleaseWindow, testBillingPeriod, true)
	if d != DecisionRelease {
		t.Fatalf("Decide() = %v, want DecisionRelease for an idle node inside the release window", d)
	}
}

func TestDecide_ExactlyAtWindowBoundaryReleases(t *testing.T) {
	// now == renewal - releaseWindow is the inclusive boundary (the window
	// "opens" at exactly this instant).
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-testReleaseWindow)
	d, _ := Decide(created, now, testReleaseWindow, testBillingPeriod, true)
	if d != DecisionRelease {
		t.Fatalf("Decide() = %v, want DecisionRelease exactly at the window boundary", d)
	}
}

func TestDecide_JustBeforeWindowBoundaryKeeps(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-testReleaseWindow).Add(-time.Second)
	d, _ := Decide(created, now, testReleaseWindow, testBillingPeriod, true)
	if d != DecisionKeep {
		t.Fatalf("Decide() = %v, want DecisionKeep one second before the window opens", d)
	}
}

func TestDecide_PastRenewalStillIdleReleases(t *testing.T) {
	// Defensive case: if the reaper somehow runs late (missed CronJob tick)
	// and now is already past the projected renewal, an idle instance is
	// still released rather than left to actually renew.
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(2 * time.Hour)
	d, _ := Decide(created, now, testReleaseWindow, testBillingPeriod, true)
	if d != DecisionRelease {
		t.Fatalf("Decide() = %v, want DecisionRelease when now is already past renewal", d)
	}
}

// --- Pod classification helpers ---------------------------------------

func daemonSetPod(name string) corev1.Pod {
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: "kube-system",
			OwnerReferences: []metav1.OwnerReference{
				{Kind: "DaemonSet", Name: "some-ds"},
			},
		},
		Spec: corev1.PodSpec{NodeName: "node-under-test"},
	}
}

func mirrorPod(name string) corev1.Pod {
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:        name,
			Namespace:   "kube-system",
			Annotations: map[string]string{mirrorPodAnnotation: "hash"},
		},
		Spec: corev1.PodSpec{NodeName: "node-under-test"},
	}
}

func overprovisioningPod(name string) corev1.Pod {
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "fuzeinfra"},
		Spec: corev1.PodSpec{
			NodeName:          "node-under-test",
			PriorityClassName: overprovisioningPriorityClass,
		},
	}
}

func workloadPod(name string) corev1.Pod {
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: "default",
			OwnerReferences: []metav1.OwnerReference{
				{Kind: "ReplicaSet", Name: "some-deploy-abc123"},
			},
		},
		Spec: corev1.PodSpec{NodeName: "node-under-test"},
	}
}

func TestIsIdleIgnorable(t *testing.T) {
	cases := []struct {
		name string
		pod  corev1.Pod
		want bool
	}{
		{"daemonset", daemonSetPod("ds-1"), true},
		{"mirror", mirrorPod("mirror-1"), true},
		{"overprovisioning", overprovisioningPod("balloon-1"), true},
		{"real workload", workloadPod("app-1"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isIdleIgnorable(tc.pod); got != tc.want {
				t.Errorf("isIdleIgnorable(%s) = %v, want %v", tc.name, got, tc.want)
			}
		})
	}
}

// --- Ready node with a fake clientset ------------------------------------

func readyElasticNode(name string) *corev1.Node {
	return &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{
			Name:   name,
			Labels: map[string]string{elasticPoolLabel: elasticPoolValue},
		},
		Status: corev1.NodeStatus{
			Conditions: []corev1.NodeCondition{
				{Type: corev1.NodeReady, Status: corev1.ConditionTrue},
			},
		},
	}
}

// fakeContabo is a minimal contabo.Client stub for exercising Reaper.Run
// without touching the real HTTP client or the k8s API.
type fakeContabo struct {
	instances []contabo.Instance
	listErr   error
	deleteErr error
	deleted   []int64
}

func (f *fakeContabo) ListByTag(_ context.Context, _ string) ([]contabo.Instance, error) {
	return f.instances, f.listErr
}

func (f *fakeContabo) Create(_ context.Context, _ contabo.CreateReq) (contabo.Instance, error) {
	return contabo.Instance{}, errors.New("fakeContabo.Create not implemented")
}

func (f *fakeContabo) Delete(_ context.Context, id int64) error {
	if f.deleteErr != nil {
		return f.deleteErr
	}
	f.deleted = append(f.deleted, id)
	return nil
}

func TestRun_IdleNodeInWindowIsReleased(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour)

	inst := contabo.Instance{ID: 42, Name: "fuzeinfra-elastic-0", Status: "running", CreatedDate: created}
	node := readyElasticNode("fuzeinfra-elastic-0")
	ds := daemonSetPod("kube-proxy-abc")

	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	k8s := k8sfake.NewSimpleClientset(node, &ds)

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic", EvictionTimeout: 5 * time.Second},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 1 || summary.Kept != 0 || summary.Errors != 0 {
		t.Fatalf("summary = %+v, want Released=1 Kept=0 Errors=0", summary)
	}
	if len(fc.deleted) != 1 || fc.deleted[0] != 42 {
		t.Fatalf("expected Contabo.Delete(42) to have been called, deleted=%v", fc.deleted)
	}

	gotNode, err := k8s.CoreV1().Nodes().Get(context.Background(), "fuzeinfra-elastic-0", metav1.GetOptions{})
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if !gotNode.Spec.Unschedulable {
		t.Fatal("expected the node to have been cordoned (Spec.Unschedulable=true)")
	}
}

func TestRun_BusyNodeInWindowIsKeptAndNotDeleted(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour)

	inst := contabo.Instance{ID: 43, Name: "fuzeinfra-elastic-1", Status: "running", CreatedDate: created}
	node := readyElasticNode("fuzeinfra-elastic-1")
	app := workloadPod("app-1")

	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	k8s := k8sfake.NewSimpleClientset(node, &app)

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic", EvictionTimeout: 5 * time.Second},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 0 || summary.Kept != 1 {
		t.Fatalf("summary = %+v, want Released=0 Kept=1", summary)
	}
	if len(fc.deleted) != 0 {
		t.Fatalf("expected NO Contabo.Delete calls for a busy node, deleted=%v", fc.deleted)
	}

	gotNode, err := k8s.CoreV1().Nodes().Get(context.Background(), "fuzeinfra-elastic-1", metav1.GetOptions{})
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if gotNode.Spec.Unschedulable {
		t.Fatal("expected the busy node NOT to be cordoned")
	}
}

func TestRun_OutsideWindowIsKeptWithoutTouchingNodes(t *testing.T) {
	created := time.Date(2026, 7, 1, 0, 0, 0, 0, time.UTC) // renewal ~30d out
	now := created.Add(24 * time.Hour)

	inst := contabo.Instance{ID: 44, Name: "fuzeinfra-elastic-2", CreatedDate: created}
	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	// No matching k8s Node registered at all — if decideByRenewal correctly
	// short-circuits before any node lookup, Run must still succeed (a Get
	// against a nonexistent node would otherwise surface as a NotFound
	// skip, which is a different code path than intended here).
	k8s := k8sfake.NewSimpleClientset()

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic"},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 0 || summary.Kept != 1 {
		t.Fatalf("summary = %+v, want Released=0 Kept=1", summary)
	}
	if len(fc.deleted) != 0 {
		t.Fatalf("expected no deletes, got %v", fc.deleted)
	}
}

func TestRun_NoMatchingNodeIsKeptNotCanceled(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour)

	// Instance is within the release window, but never joined the cluster —
	// no Node named fuzeinfra-elastic-3 exists.
	inst := contabo.Instance{ID: 45, Name: "fuzeinfra-elastic-3", CreatedDate: created}
	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	k8s := k8sfake.NewSimpleClientset()

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic"},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 0 || summary.Kept != 1 {
		t.Fatalf("summary = %+v, want Released=0 Kept=1 for a never-joined instance", summary)
	}
	if len(fc.deleted) != 0 {
		t.Fatalf("expected instance 45 to NEVER be canceled without a matching node, deleted=%v", fc.deleted)
	}
}

func TestRun_NotReadyNodeIsKeptNotCanceled(t *testing.T) {
	created := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	renewal := created.Add(testBillingPeriod)
	now := renewal.Add(-1 * time.Hour)

	inst := contabo.Instance{ID: 46, Name: "fuzeinfra-elastic-4", CreatedDate: created}
	node := &corev1.Node{
		ObjectMeta: metav1.ObjectMeta{Name: "fuzeinfra-elastic-4", Labels: map[string]string{elasticPoolLabel: elasticPoolValue}},
		Status: corev1.NodeStatus{
			Conditions: []corev1.NodeCondition{{Type: corev1.NodeReady, Status: corev1.ConditionFalse}},
		},
	}

	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	k8s := k8sfake.NewSimpleClientset(node)

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic"},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 0 || summary.Kept != 1 {
		t.Fatalf("summary = %+v, want Released=0 Kept=1 for a NotReady node", summary)
	}
	if len(fc.deleted) != 0 {
		t.Fatalf("expected instance 46 to NEVER be canceled while its node is NotReady, deleted=%v", fc.deleted)
	}
}

func TestRun_UnknownCreatedDateIsKeptNotCanceled(t *testing.T) {
	// Even if the matching node exists, is Ready, and is idle, an instance
	// whose createdDate never parsed must never be reaped — the reaper has
	// no billing anchor to reason about.
	now := time.Date(2026, 7, 22, 0, 0, 0, 0, time.UTC)

	inst := contabo.Instance{ID: 47, Name: "fuzeinfra-elastic-5"} // CreatedDate zero value
	node := readyElasticNode("fuzeinfra-elastic-5")

	fc := &fakeContabo{instances: []contabo.Instance{inst}}
	k8s := k8sfake.NewSimpleClientset(node)

	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     Config{ReleaseWindow: testReleaseWindow, BillingPeriod: testBillingPeriod, ElasticTag: "fuzeinfra-elastic"},
		Now:     func() time.Time { return now },
	}

	summary, err := r.Run(context.Background())
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if summary.Released != 0 || summary.Kept != 1 {
		t.Fatalf("summary = %+v, want Released=0 Kept=1 for an unparsable createdDate", summary)
	}
	if len(fc.deleted) != 0 {
		t.Fatalf("expected instance 47 to NEVER be canceled with a zero CreatedDate, deleted=%v", fc.deleted)
	}
}

func TestRun_ListByTagErrorPropagates(t *testing.T) {
	fc := &fakeContabo{listErr: errors.New("contabo API down")}
	k8s := k8sfake.NewSimpleClientset()
	r := &Reaper{
		Contabo: fc,
		K8s:     k8s,
		Cfg:     DefaultConfig(),
		Now:     time.Now,
	}
	if _, err := r.Run(context.Background()); err == nil {
		t.Fatal("expected Run to propagate a ListByTag error")
	}
}
