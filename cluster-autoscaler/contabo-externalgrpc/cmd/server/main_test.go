package main

import (
	"testing"
)

// fullEnv returns a fully-populated, valid environment map covering every
// required variable plus a couple of optional overrides, used as the base
// for table-test cases that tweak individual keys.
func fullEnv() map[string]string {
	return map[string]string{
		"CONTABO_CLIENT_ID":     "client-id",
		"CONTABO_CLIENT_SECRET": "client-secret",
		"CONTABO_API_USER":      "api-user",
		"CONTABO_API_PASSWORD":  "api-password",
		"K3S_SERVER_URL":        "https://k3s.example.com:6443",
		"K3S_NODE_TOKEN":        "node-token",
		"PRODUCT_ID":            "V45",
		"IMAGE_ID":              "img-123",
		"REGION":                "EU",
		"SSH_KEY_ID":            "12345",
		"MIN_SIZE":              "1",
		"MAX_SIZE":              "5",
	}
}

func getenvFromMap(m map[string]string) func(string) string {
	return func(key string) string {
		return m[key]
	}
}

func TestLoadConfig_FullyPopulated(t *testing.T) {
	env := fullEnv()
	provCfg, contaboCfg, grpcListen, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig returned unexpected error: %v", err)
	}

	if provCfg.MinSize != 1 {
		t.Errorf("MinSize = %d, want 1", provCfg.MinSize)
	}
	if provCfg.MaxSize != 5 {
		t.Errorf("MaxSize = %d, want 5", provCfg.MaxSize)
	}
	if provCfg.SSHKeyID != 12345 {
		t.Errorf("SSHKeyID = %d, want 12345", provCfg.SSHKeyID)
	}
	if provCfg.ProductID != "V45" {
		t.Errorf("ProductID = %q, want V45", provCfg.ProductID)
	}
	if provCfg.ImageID != "img-123" {
		t.Errorf("ImageID = %q, want img-123", provCfg.ImageID)
	}
	if provCfg.Region != "EU" {
		t.Errorf("Region = %q, want EU", provCfg.Region)
	}

	// Defaults applied for optional vars not set in fullEnv.
	if provCfg.ElasticTag != defaultElasticTag {
		t.Errorf("ElasticTag = %q, want default %q", provCfg.ElasticTag, defaultElasticTag)
	}
	if provCfg.NamePrefix != defaultNamePrefix {
		t.Errorf("NamePrefix = %q, want default %q", provCfg.NamePrefix, defaultNamePrefix)
	}
	if provCfg.UserDataTmpl != "" {
		t.Errorf("UserDataTmpl = %q, want empty (no USER_DATA_TEMPLATE_B64 set)", provCfg.UserDataTmpl)
	}

	// K3S join parameters are threaded onto the provider config so the
	// cloud-init template can reference them ({{.K3SServerURL}}/{{.K3SNodeToken}}).
	if provCfg.K3SServerURL != "https://k3s.example.com:6443" {
		t.Errorf("K3SServerURL = %q, want https://k3s.example.com:6443", provCfg.K3SServerURL)
	}
	if provCfg.K3SNodeToken != "node-token" {
		t.Errorf("K3SNodeToken = %q, want node-token", provCfg.K3SNodeToken)
	}

	if contaboCfg.ClientID != "client-id" {
		t.Errorf("ClientID = %q, want client-id", contaboCfg.ClientID)
	}
	if contaboCfg.ClientSecret != "client-secret" {
		t.Errorf("ClientSecret = %q, want client-secret", contaboCfg.ClientSecret)
	}
	if contaboCfg.User != "api-user" {
		t.Errorf("User = %q, want api-user", contaboCfg.User)
	}
	if contaboCfg.Pass != "api-password" {
		t.Errorf("Pass = %q, want api-password", contaboCfg.Pass)
	}
	if contaboCfg.BaseURL != defaultContaboBaseURL {
		t.Errorf("BaseURL = %q, want default %q", contaboCfg.BaseURL, defaultContaboBaseURL)
	}
	if contaboCfg.AuthURL != defaultContaboAuthURL {
		t.Errorf("AuthURL = %q, want default %q", contaboCfg.AuthURL, defaultContaboAuthURL)
	}

	if grpcListen != defaultGRPCListen {
		t.Errorf("grpcListen = %q, want default %q", grpcListen, defaultGRPCListen)
	}
}

func TestLoadConfig_UserDataTemplateDecoded(t *testing.T) {
	env := fullEnv()
	// base64("k3s-url={{.NodeName}}") — verifies loadConfig only base64-decodes
	// the template and stores it verbatim; template rendering happens later in
	// the provider (see renderUserData), not here.
	env["USER_DATA_TEMPLATE_B64"] = "azNzLXVybD17ey5Ob2RlTmFtZX19"
	provCfg, _, _, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig returned unexpected error: %v", err)
	}
	want := "k3s-url={{.NodeName}}"
	if provCfg.UserDataTmpl != want {
		t.Errorf("UserDataTmpl = %q, want %q", provCfg.UserDataTmpl, want)
	}
}

func TestLoadConfig_MissingRequiredVar(t *testing.T) {
	requiredKeys := []string{
		"CONTABO_CLIENT_ID",
		"CONTABO_CLIENT_SECRET",
		"CONTABO_API_USER",
		"CONTABO_API_PASSWORD",
		"K3S_SERVER_URL",
		"K3S_NODE_TOKEN",
		"PRODUCT_ID",
		"IMAGE_ID",
		"REGION",
	}

	for _, key := range requiredKeys {
		t.Run(key, func(t *testing.T) {
			env := fullEnv()
			delete(env, key)
			_, _, _, err := loadConfig(getenvFromMap(env))
			if err == nil {
				t.Fatalf("loadConfig with missing %s: want error, got nil", key)
			}
		})
	}
}

func TestLoadConfig_MinSizeGreaterThanMaxSize(t *testing.T) {
	env := fullEnv()
	env["MIN_SIZE"] = "10"
	env["MAX_SIZE"] = "5"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err == nil {
		t.Fatalf("loadConfig with MIN_SIZE > MAX_SIZE: want error, got nil")
	}
}

func TestLoadConfig_NonNumericMaxSize(t *testing.T) {
	env := fullEnv()
	env["MAX_SIZE"] = "not-a-number"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err == nil {
		t.Fatalf("loadConfig with non-numeric MAX_SIZE: want error, got nil")
	}
}

func TestLoadConfig_NonNumericMinSize(t *testing.T) {
	env := fullEnv()
	env["MIN_SIZE"] = "not-a-number"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err == nil {
		t.Fatalf("loadConfig with non-numeric MIN_SIZE: want error, got nil")
	}
}

func TestLoadConfig_NonNumericSSHKeyID(t *testing.T) {
	env := fullEnv()
	env["SSH_KEY_ID"] = "not-a-number"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err == nil {
		t.Fatalf("loadConfig with non-numeric SSH_KEY_ID: want error, got nil")
	}
}

// TestLoadConfig_SSHKeyIDDefaultsToZeroWhenUnset covers the elastic-node
// SSH-via-cloud-init decision: the FuzeInfra Contabo account has zero
// registered SSH-key secrets, so SSH_KEY_ID is no longer a required env var.
// When it's absent entirely, loadConfig must succeed with SSHKeyID=0 (which
// internal/contabo/client.go's Create() then treats as "omit sshKeys").
func TestLoadConfig_SSHKeyIDDefaultsToZeroWhenUnset(t *testing.T) {
	env := fullEnv()
	delete(env, "SSH_KEY_ID")
	provCfg, _, _, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig with SSH_KEY_ID unset: want success, got error: %v", err)
	}
	if provCfg.SSHKeyID != 0 {
		t.Errorf("SSHKeyID = %d, want 0 (default when SSH_KEY_ID is unset)", provCfg.SSHKeyID)
	}
}

// TestLoadConfig_SSHKeyIDEmptyStringTreatedAsUnset covers the case where the
// SSH_KEY_ID key is present in the environment (e.g. sourced from a
// SealedSecret key that resolves to an empty value) but its value is the
// empty string — same defaulting behavior as fully unset.
func TestLoadConfig_SSHKeyIDEmptyStringTreatedAsUnset(t *testing.T) {
	env := fullEnv()
	env["SSH_KEY_ID"] = ""
	provCfg, _, _, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig with SSH_KEY_ID=\"\": want success, got error: %v", err)
	}
	if provCfg.SSHKeyID != 0 {
		t.Errorf("SSHKeyID = %d, want 0 (default when SSH_KEY_ID is empty)", provCfg.SSHKeyID)
	}
}

func TestLoadConfig_MaxSizeZero(t *testing.T) {
	env := fullEnv()
	env["MIN_SIZE"] = "0"
	env["MAX_SIZE"] = "0"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err == nil {
		t.Fatalf("loadConfig with MAX_SIZE=0: want error, got nil")
	}
}

// fakeCloudEnv returns a minimal environment for FAKE_CLOUD=1 mode: no
// Contabo/K3S credentials, but with the provider-config fields that must
// still be validated regardless of fake/real mode.
func fakeCloudEnv() map[string]string {
	return map[string]string{
		"FAKE_CLOUD": "1",
		"PRODUCT_ID": "V45",
		"IMAGE_ID":   "img-123",
		"REGION":     "EU",
		"SSH_KEY_ID": "12345",
		"MIN_SIZE":   "1",
		"MAX_SIZE":   "5",
	}
}

func TestLoadConfig_FakeCloud_SucceedsWithoutContaboOrK3SCreds(t *testing.T) {
	env := fakeCloudEnv()
	provCfg, _, _, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig with FAKE_CLOUD=1 and no Contabo/K3S creds: want success, got error: %v", err)
	}

	if provCfg.ProductID != "V45" {
		t.Errorf("ProductID = %q, want V45", provCfg.ProductID)
	}
	if provCfg.ImageID != "img-123" {
		t.Errorf("ImageID = %q, want img-123", provCfg.ImageID)
	}
	if provCfg.Region != "EU" {
		t.Errorf("Region = %q, want EU", provCfg.Region)
	}
	if provCfg.MinSize != 1 {
		t.Errorf("MinSize = %d, want 1", provCfg.MinSize)
	}
	if provCfg.MaxSize != 5 {
		t.Errorf("MaxSize = %d, want 5", provCfg.MaxSize)
	}
}

func TestLoadConfig_FakeCloud_TrueVariant(t *testing.T) {
	env := fakeCloudEnv()
	env["FAKE_CLOUD"] = "true"
	_, _, _, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig with FAKE_CLOUD=true: want success, got error: %v", err)
	}
}

func TestLoadConfig_FakeCloud_StillRequiresProviderFields(t *testing.T) {
	requiredKeys := []string{
		"PRODUCT_ID",
		"IMAGE_ID",
		"REGION",
	}

	for _, key := range requiredKeys {
		t.Run(key, func(t *testing.T) {
			env := fakeCloudEnv()
			delete(env, key)
			_, _, _, err := loadConfig(getenvFromMap(env))
			if err == nil {
				t.Fatalf("loadConfig with FAKE_CLOUD=1 and missing %s: want error, got nil", key)
			}
		})
	}
}

func TestLoadConfig_RealMode_StillRequiresContaboAndK3SCreds(t *testing.T) {
	// FAKE_CLOUD unset (real mode): the full credential set from fullEnv is
	// still mandatory. This mirrors TestLoadConfig_MissingRequiredVar but
	// pins down that FAKE_CLOUD's relaxation does NOT leak into real mode.
	requiredCredKeys := []string{
		"CONTABO_CLIENT_ID",
		"CONTABO_CLIENT_SECRET",
		"CONTABO_API_USER",
		"CONTABO_API_PASSWORD",
		"K3S_SERVER_URL",
		"K3S_NODE_TOKEN",
	}

	for _, key := range requiredCredKeys {
		t.Run(key, func(t *testing.T) {
			env := fullEnv()
			// FAKE_CLOUD explicitly unset/false: env map simply omits it.
			delete(env, key)
			_, _, _, err := loadConfig(getenvFromMap(env))
			if err == nil {
				t.Fatalf("loadConfig in real mode with missing %s: want error, got nil", key)
			}
		})
	}
}

func TestLoadConfig_FakeCloudFalseVariantsBehaveAsRealMode(t *testing.T) {
	falseVariants := []string{"", "0", "false", "no", "FAKE"}
	for _, v := range falseVariants {
		t.Run(v, func(t *testing.T) {
			env := fullEnv()
			env["FAKE_CLOUD"] = v
			delete(env, "CONTABO_CLIENT_ID")
			_, _, _, err := loadConfig(getenvFromMap(env))
			if err == nil {
				t.Fatalf("loadConfig with FAKE_CLOUD=%q and missing CONTABO_CLIENT_ID: want error, got nil", v)
			}
		})
	}
}
