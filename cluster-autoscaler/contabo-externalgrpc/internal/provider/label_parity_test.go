package provider_test

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
)

// elasticUserDataTemplates lists every cloud-init template that provisions an
// ELASTIC (autoscaler-managed) node. All of them must apply the same
// --node-label set, because Cluster Autoscaler simulates all of them with the
// single NodeGroupTemplateNodeInfo node built in template.go — it has no way to
// know which variant an operator base64'd into userDataTemplateB64.
//
// durable-userdata-eth1.template is deliberately excluded: it provisions the
// durable pool (fuzeinfra.io/pool=durable), which the elastic node group's
// template does not describe.
var elasticUserDataTemplates = []string{
	"elastic-userdata.template",
	"elastic-userdata-eth1.template",
	"elastic-userdata-privnet.template",
}

// nodeLabelFlagRe matches a `--node-label 'key=value'` flag as written in the
// k3s agent join line of the cloud-init templates.
//
// DELIBERATELY OUTSIDE THIS CONTRACT: fuzeinfra.io/vlan. The elastic join line
// passes it via the shell variable $VLANARGS, whose value depends on whether the
// private NIC came up, so it is a runtime STATUS label rather than a scheduling
// label -- a quarantined node carries vlan=absent plus a NoSchedule taint, and
// the CA simulation must describe the node CA expects to get, not that one. No
// workload selects on it, so excluding it cannot cause the scale-up/scale-down
// mismatches this test exists to prevent. Alerting on it lives in
// helm/fuzeinfra/rules/nodes.yml, not here.
var nodeLabelFlagRe = regexp.MustCompile(`--node-label\s+'([^'=]+)=([^']*)'`)

// TestElasticNodeLabelParity is the drift guard that replaces a comment.
//
// Two halves have to agree for elastic autoscaling to work at all:
//
//   - deploy/elastic-userdata*.template  — the labels a REAL elastic node gets
//     from k3s at agent registration.
//   - internal/provider/template.go      — the labels on the SIMULATED node CA
//     schedules pending pods against when deciding whether to scale up from zero.
//
// If cloud-init grows a label that the simulation lacks, CA refuses to scale up
// for pods that select it (the node it would create would have accepted them).
// If the simulation grows a label cloud-init lacks, CA scales up for pods that
// then go unschedulable on the node it just paid for. Both failure modes are
// silent, which is why this is a test and not a comment.
func TestElasticNodeLabelParity(t *testing.T) {
	simulated := simulatedNodeLabels(t)

	// Guard against the degenerate "both sides empty, so they match" pass, and
	// pin the two labels this node group's contract actually depends on.
	for k, want := range map[string]string{
		"fuzeinfra.io/pool": "elastic",
		"node-role":         "workload",
	} {
		if got, ok := simulated[k]; !ok || got != want {
			t.Errorf("simulated node (template.go) label %q = %q (present=%v), want %q", k, got, ok, want)
		}
	}

	for _, name := range elasticUserDataTemplates {
		cloudInit := cloudInitNodeLabels(t, name)
		if !reflect.DeepEqual(cloudInit, simulated) {
			t.Errorf(
				"label drift between deploy/%s and internal/provider/template.go:\n"+
					"  cloud-init (--node-label flags): %s\n"+
					"  CA simulation (Labels map):      %s\n"+
					"Cluster Autoscaler schedules pending pods against the simulated node, "+
					"so a label present on only one side breaks scale-up decisions silently. "+
					"Change both together.",
				name, formatLabels(cloudInit), formatLabels(simulated),
			)
		}
	}
}

// simulatedNodeLabels returns the labels NodeGroupTemplateNodeInfo puts on its
// synthetic node, minus the well-known kubernetes.io/* labels — those are set by
// the kubelet itself, not by a --node-label flag, so they have no counterpart in
// cloud-init and are not part of the parity contract.
func simulatedNodeLabels(t *testing.T) map[string]string {
	t.Helper()

	s := provider.New(provider.Config{
		ProductID:  "V153",
		NamePrefix: "fuzeinfra-prod-elastic-v2",
		MinSize:    0,
		MaxSize:    4,
	}, &fakeCloud{})

	resp, err := s.NodeGroupTemplateNodeInfo(context.Background(), &protos.NodeGroupTemplateNodeInfoRequest{Id: "elastic"})
	if err != nil {
		t.Fatalf("NodeGroupTemplateNodeInfo: %v", err)
	}
	if resp.NodeInfo == nil {
		t.Fatal("NodeGroupTemplateNodeInfo returned a nil NodeInfo")
	}

	out := make(map[string]string, len(resp.NodeInfo.Labels))
	for k, v := range resp.NodeInfo.Labels {
		if strings.HasPrefix(k, "kubernetes.io/") {
			continue
		}
		out[k] = v
	}
	return out
}

// cloudInitNodeLabels parses the --node-label flags out of the k3s agent join
// line of a cloud-init userdata template.
func cloudInitNodeLabels(t *testing.T, name string) map[string]string {
	t.Helper()

	path := deployTemplatePath(t, name)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}

	joinLine := ""
	for _, line := range strings.Split(string(raw), "\n") {
		// The join line is the only one that installs the k3s agent; comment
		// lines that merely mention --node-label must not be picked up.
		if strings.Contains(line, "get.k3s.io") && strings.Contains(line, " agent ") {
			if joinLine != "" {
				t.Fatalf("%s: found more than one k3s agent join line; the parser assumes exactly one", name)
			}
			joinLine = line
		}
	}
	if joinLine == "" {
		t.Fatalf("%s: no k3s agent join line found (looked for a line containing get.k3s.io and ' agent ')", name)
	}

	out := map[string]string{}
	for _, m := range nodeLabelFlagRe.FindAllStringSubmatch(joinLine, -1) {
		if prev, dup := out[m[1]]; dup {
			t.Errorf("%s: --node-label %s specified twice (%q then %q)", name, m[1], prev, m[2])
		}
		out[m[1]] = m[2]
	}
	if len(out) == 0 {
		t.Fatalf("%s: parsed zero --node-label flags from the join line: %s", name, joinLine)
	}
	return out
}

// deployTemplatePath resolves a file in cluster-autoscaler/contabo-externalgrpc/deploy/
// relative to this test file (internal/provider/ -> internal -> module root -> deploy).
func deployTemplatePath(t *testing.T, name string) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed to resolve this test file's location")
	}
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "deploy", name)
}

func formatLabels(m map[string]string) string {
	parts := make([]string, 0, len(m))
	for k, v := range m {
		parts = append(parts, k+"="+v)
	}
	sort.Strings(parts)
	return "{" + strings.Join(parts, ", ") + "}"
}
