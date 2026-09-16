package provider_test

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
)

// valuesContaboRelPath is the prod overlay that carries the ONLY copy of the
// elastic cloud-init that prod actually uses.
const valuesContaboRelPath = "helm/fuzeinfra/values-contabo.yaml"

var userDataB64Re = regexp.MustCompile(`userDataTemplateB64:\s*"([A-Za-z0-9+/=]+)"`)

// TestValuesContaboUserDataB64Parity closes the fourth silent-drift site.
//
// deploy/elastic-userdata.template is a SOURCE artifact — the provider never
// reads it. What prod actually ships is the base64 blob in
// values-contabo.yaml's clusterAutoscaler.provider.userDataTemplateB64, which
// ca-cutover.yml produces from the template with NODE_SSH_PUBLIC_KEY
// substituted. So editing the template alone changes nothing about a real
// elastic node; the edit has to reach the base64 too, and nothing enforced that.
//
// This asserts the k3s agent join line — the only functional line, and the one
// with no secret in it — is byte-identical on both sides. Everything else in the
// blob (the SSH key, and any commented-out opt-in block) is deliberately allowed
// to differ, so this test never needs to see a credential.
func TestValuesContaboUserDataB64Parity(t *testing.T) {
	valuesPath := repoFile(t, valuesContaboRelPath)

	valuesRaw, err := os.ReadFile(valuesPath)
	if err != nil {
		t.Fatalf("reading %s: %v", valuesPath, err)
	}
	m := userDataB64Re.FindSubmatch(valuesRaw)
	if m == nil {
		t.Fatalf("%s: no clusterAutoscaler.provider.userDataTemplateB64 found", valuesContaboRelPath)
	}
	decoded, err := base64.StdEncoding.DecodeString(string(m[1]))
	if err != nil {
		t.Fatalf("%s: userDataTemplateB64 is not valid base64: %v", valuesContaboRelPath, err)
	}

	tmplRaw, err := os.ReadFile(deployTemplatePath(t, "elastic-userdata.template"))
	if err != nil {
		t.Fatalf("reading elastic-userdata.template: %v", err)
	}

	shipped := k3sJoinLine(t, string(decoded), valuesContaboRelPath+" (decoded userDataTemplateB64)")
	source := k3sJoinLine(t, string(tmplRaw), "deploy/elastic-userdata.template")

	if shipped != source {
		t.Errorf(
			"the k3s join line prod ships differs from the source template.\n"+
				"  %s:\n    %s\n"+
				"  %s:\n    %s\n"+
				"The provider renders the BASE64 blob, not the template file, so a template-only "+
				"edit is a no-op on a real elastic node. Re-encode the template into "+
				"userDataTemplateB64 (preserving the substituted NODE_SSH_PUBLIC_KEY) and try again.",
			"deploy/elastic-userdata.template", source,
			valuesContaboRelPath, shipped,
		)
	}

	// Belt and braces: whatever else is true, the shipped blob must carry the
	// label the product workloads select on. k3s applies --node-label at agent
	// registration only, so losing it here cannot be repaired by relabelling.
	if !strings.Contains(shipped, "--node-label 'node-role=workload'") {
		t.Errorf("the shipped join line does not apply node-role=workload:\n  %s", shipped)
	}
}

// k3sJoinLine returns the single cloud-init runcmd line that installs the k3s agent.
func k3sJoinLine(t *testing.T, doc, what string) string {
	t.Helper()
	found := ""
	for _, line := range strings.Split(strings.ReplaceAll(doc, "\r\n", "\n"), "\n") {
		if strings.Contains(line, "get.k3s.io") && strings.Contains(line, " agent ") {
			if found != "" {
				t.Fatalf("%s: more than one k3s agent join line", what)
			}
			found = strings.TrimRight(line, " \t")
		}
	}
	if found == "" {
		t.Fatalf("%s: no k3s agent join line found", what)
	}
	return found
}

// repoFile resolves a repo-root-relative path by walking up from this test file
// until the path exists. The Go module lives at
// cluster-autoscaler/contabo-externalgrpc/, several levels below the repo root.
func repoFile(t *testing.T, rel string) string {
	t.Helper()
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed to resolve this test file's location")
	}
	dir := filepath.Dir(thisFile)
	for i := 0; i < 12; i++ {
		candidate := filepath.Join(dir, rel)
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	t.Fatalf("could not locate %s by walking up from %s — this test must run inside a full FuzeInfra checkout, not a module-only build context", rel, filepath.Dir(thisFile))
	return ""
}
