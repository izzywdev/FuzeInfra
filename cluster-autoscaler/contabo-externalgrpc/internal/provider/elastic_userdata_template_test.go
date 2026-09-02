package provider

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"gopkg.in/yaml.v2"
)

// TestElasticUserDataTemplate_RendersValidCloudConfig proves that
// deploy/elastic-userdata.template — the canonical source ca-cutover.yml
// base64-encodes into clusterAutoscaler.provider.userDataTemplateB64 — still
// renders to syntactically valid #cloud-config YAML once its Go-template
// vars ({{.NodeName}}/{{.K3SServerURL}}/{{.K3SNodeToken}}) and the
// __SSH_PUBLIC_KEY__ placeholder are substituted exactly as renderUserData
// and ca-cutover.yml do it.
//
// This guards against the failure mode a real elastic scale-up hit: a VPS
// created from a userdata blob that cloud-init can't parse as valid YAML
// aborts before runcmd ever executes, so k3s is never installed and the node
// never joins — silently, with no error surfaced to the autoscaler.
func TestElasticUserDataTemplate_RendersValidCloudConfig(t *testing.T) {
	tmplPath := elasticUserDataTemplatePath(t)
	raw, err := os.ReadFile(tmplPath)
	if err != nil {
		t.Fatalf("reading %s: %v", tmplPath, err)
	}

	rendered, err := renderUserData(
		string(raw),
		"fuzeinfra-elastic-a1b2c3d4",
		"https://161.97.118.134:6443",
		"K10stubtoken::server:stub",
	)
	if err != nil {
		t.Fatalf("renderUserData: %v", err)
	}

	// Stub the SSH-key placeholder the same way ca-cutover.yml's Python step
	// does (content.replace("__SSH_PUBLIC_KEY__", key)), so this fixture
	// matches what actually gets base64-encoded into userDataTemplateB64.
	const stubKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIstubstubstubstubstubstubstubstubstub ci-stub"
	rendered = strings.ReplaceAll(rendered, "__SSH_PUBLIC_KEY__", stubKey)

	if strings.Contains(rendered, "__SSH_PUBLIC_KEY__") {
		t.Fatalf("stub substitution left __SSH_PUBLIC_KEY__ unresolved:\n%s", rendered)
	}
	if strings.Contains(rendered, "{{") || strings.Contains(rendered, "}}") {
		t.Fatalf("rendered userdata still contains unrendered template actions:\n%s", rendered)
	}
	if !strings.HasPrefix(rendered, "#cloud-config") {
		t.Fatalf("rendered userdata must start with #cloud-config (required by cloud-init to select the cloud-config handler), got:\n%.80s", rendered)
	}

	var doc map[string]interface{}
	if err := yaml.Unmarshal([]byte(rendered), &doc); err != nil {
		t.Fatalf("rendered userdata is not valid YAML: %v\n--- rendered ---\n%s", err, rendered)
	}

	for _, key := range []string{"users", "write_files", "runcmd"} {
		if _, ok := doc[key]; !ok {
			t.Errorf("rendered cloud-config missing expected top-level key %q", key)
		}
	}

	// The join command is the single point of failure for cluster membership:
	// assert the substituted values and the required join flags all made it
	// into the rendered runcmd, so a future edit can't silently drop one.
	runcmd, ok := doc["runcmd"].([]interface{})
	if !ok || len(runcmd) == 0 {
		t.Fatalf("want a non-empty runcmd list, got %#v", doc["runcmd"])
	}
	joinLine := renderedRuncmdShellString(t, runcmd[len(runcmd)-1])
	for _, want := range []string{
		"K3S_URL=https://161.97.118.134:6443",
		"K3S_TOKEN=K10stubtoken::server:stub",
		"--node-name 'fuzeinfra-elastic-a1b2c3d4'",
		"--kubelet-arg 'provider-id=contabo://fuzeinfra-elastic-a1b2c3d4'",
		"--node-label 'fuzeinfra.io/pool=elastic'",
		// node-role=workload is the label the product workloads' nodeSelector
		// targets. k3s only applies --node-label at agent REGISTRATION, so if
		// this flag is ever dropped the label cannot be recovered by relabelling
		// -- every node the pool creates from then on is unschedulable for
		// fuzekeys/fuzebi/fuzehub/fuzedeploy/fuzeexecutive, and a hand-applied
		// `kubectl label` evaporates at the next scale event.
		// TestElasticNodeLabelParity additionally checks this against the CA
		// scale-from-zero simulation in template.go.
		"--node-label 'node-role=workload'",
		"--node-taint 'fuzeinfra.io/elastic=true:PreferNoSchedule'",
		// Pinned to the same channel as the baseline module (v1.36, per
		// #318/#366) rather than a floating "stable" channel, so an elastic
		// agent joins in lockstep with the rest of the fleet.
		"INSTALL_K3S_CHANNEL=v1.36",
		// The live prod cluster runs flannel wireguard-native on all nodes
		// (#366), so an elastic agent must allow WireGuard traffic or it can
		// never pass pod-overlay traffic even after joining the API server.
		"ufw allow 51820/udp",
	} {
		if !strings.Contains(rendered, want) {
			t.Errorf("rendered userdata missing %q\n--- rendered ---\n%s", want, rendered)
		}
	}
	// The join deliberately does NOT live in runcmd any more.
	//
	// The Contabo private-network ASSIGN happens after createInstance returns
	// and MAY REBOOT the instance, and cloud-init `runcmd` is a once-per-instance
	// module: a reboot part-way through it can leave the join half-done and never
	// retried. The join therefore runs from a systemd oneshot that re-runs on
	// every boot until it has succeeded on the VLAN, which turns that reboot into
	// the repair trigger instead of the failure mode. Putting the installer back
	// into runcmd would silently reintroduce the race, so assert both halves:
	// runcmd only ENABLES the unit, and the installer lives in the unit's script.
	if strings.Contains(joinLine, "get.k3s.io") {
		t.Errorf("the k3s installer must not run from runcmd (a post-assign reboot would destroy it); got: %v", joinLine)
	}
	if !strings.Contains(joinLine, "systemctl enable --now fuzeinfra-vlan-join.service") {
		t.Errorf("want the last runcmd entry to enable the join unit, got: %v", joinLine)
	}

	joinScript := renderedWriteFile(t, doc, "/usr/local/sbin/fuzeinfra-vlan-join.sh")
	if !strings.Contains(joinScript, "get.k3s.io") {
		t.Errorf("the join script does not install the k3s agent:\n%s", joinScript)
	}
	unit := renderedWriteFile(t, doc, "/etc/systemd/system/fuzeinfra-vlan-join.service")
	for _, want := range []string{
		// Without this the unit runs once and a post-assign reboot never retries.
		"ConditionPathExists=!/etc/fuzeinfra-vlan-joined",
		"WantedBy=multi-user.target",
	} {
		if !strings.Contains(unit, want) {
			t.Errorf("join unit missing %q:\n%s", want, unit)
		}
	}

	// The private NIC must be CONFIGURED, not merely waited for. cloud-init's
	// fallback network config brings up only the primary NIC, so without this
	// drop-in eth1 never gets a DHCP lease and any wait for its address is
	// guaranteed to time out -- which is exactly why every elastic node joined
	// off-VLAN despite the wait added in #830.
	netplan := renderedWriteFile(t, doc, "/etc/netplan/60-eth1-private.yaml")
	for _, want := range []string{"eth1:", "dhcp4: true", "optional: true"} {
		if !strings.Contains(netplan, want) {
			t.Errorf("eth1 netplan drop-in missing %q:\n%s", want, netplan)
		}
	}
}

// renderedRuncmdShellString extracts the shell command string from a
// `[ sh, -c, "..." ]` runcmd entry as decoded by yaml.v2 (a []interface{} of
// strings).
func renderedRuncmdShellString(t *testing.T, entry interface{}) string {
	t.Helper()
	parts, ok := entry.([]interface{})
	if !ok || len(parts) != 3 {
		t.Fatalf("want a 3-element [sh, -c, \"...\"] runcmd entry, got %#v", entry)
	}
	cmd, ok := parts[2].(string)
	if !ok {
		t.Fatalf("want the third runcmd element to be a string, got %#v", parts[2])
	}
	return cmd
}

func elasticUserDataTemplatePath(t *testing.T) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed to resolve this test file's location")
	}
	// This file lives in internal/provider/; the template lives in deploy/,
	// two directories up from there (internal/provider -> internal ->
	// contabo-externalgrpc -> deploy).
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "deploy", "elastic-userdata.template")
}

// renderedWriteFile returns the content of the write_files entry at path.
func renderedWriteFile(t *testing.T, doc map[string]interface{}, path string) string {
	t.Helper()
	files, ok := doc["write_files"].([]interface{})
	if !ok {
		t.Fatalf("write_files is not a list: %#v", doc["write_files"])
	}
	for _, f := range files {
		m, ok := f.(map[interface{}]interface{})
		if !ok {
			continue
		}
		if p, _ := m["path"].(string); p == path {
			c, _ := m["content"].(string)
			return c
		}
	}
	t.Fatalf("no write_files entry for %q", path)
	return ""
}
