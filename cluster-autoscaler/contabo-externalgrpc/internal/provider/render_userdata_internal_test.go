package provider

import (
	"strings"
	"testing"
)

// TestRenderUserData_K3SFieldsSubstituted proves that renderUserData exposes
// the k3s join parameters to the template, so an operator can reference the
// join target directly instead of hand-duplicating the literal values into the
// UserData template text.
func TestRenderUserData_K3SFieldsSubstituted(t *testing.T) {
	const tmpl = "#cloud-config\n" +
		"node={{.NodeName}}\n" +
		"server={{.K3SServerURL}}\n" +
		"token={{.K3SNodeToken}}\n"

	const (
		nodeName  = "fuzeinfra-elastic-3"
		serverURL = "https://k3s.example.com:6443"
		nodeToken = "K10secrettoken::server:abc123"
	)

	out, err := renderUserData(tmpl, nodeName, serverURL, nodeToken)
	if err != nil {
		t.Fatalf("renderUserData error: %v", err)
	}

	for _, want := range []string{
		"node=" + nodeName,
		"server=" + serverURL,
		"token=" + nodeToken,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("rendered UserData missing %q\n--- got ---\n%s", want, out)
		}
	}

	// No template action markers should survive rendering.
	if strings.Contains(out, "{{") || strings.Contains(out, "}}") {
		t.Errorf("rendered UserData still contains unrendered template actions:\n%s", out)
	}
}

// TestRenderUserData_EmptyTemplate confirms an empty template short-circuits to
// an empty string regardless of the k3s parameters.
func TestRenderUserData_EmptyTemplate(t *testing.T) {
	out, err := renderUserData("", "node-0", "https://k3s.example.com:6443", "token")
	if err != nil {
		t.Fatalf("renderUserData error: %v", err)
	}
	if out != "" {
		t.Errorf("want empty UserData for empty template, got %q", out)
	}
}

// TestRenderUserData_InvalidTemplate confirms a malformed template surfaces a
// parse error rather than silently producing bad cloud-init.
func TestRenderUserData_InvalidTemplate(t *testing.T) {
	if _, err := renderUserData("{{.K3SServerURL", "node-0", "url", "token"); err == nil {
		t.Fatalf("want error for malformed template, got nil")
	}
}
