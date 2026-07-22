package main

import (
	"testing"
	"time"
)

func fullEnv() map[string]string {
	return map[string]string{
		"CONTABO_CLIENT_ID":     "client-id",
		"CONTABO_CLIENT_SECRET": "client-secret",
		"CONTABO_API_USER":      "api-user",
		"CONTABO_API_PASSWORD":  "api-password",
	}
}

func getenvFromMap(m map[string]string) func(string) string {
	return func(key string) string { return m[key] }
}

func TestLoadConfig_FullyPopulated(t *testing.T) {
	env := fullEnv()
	env["RELEASE_WINDOW"] = "48h"
	env["BILLING_PERIOD"] = "360h"
	env["ELASTIC_TAG"] = "custom-tag"
	env["CONTABO_BASE_URL"] = "https://api.example.com"
	env["CONTABO_AUTH_URL"] = "https://auth.example.com/token"

	reaperCfg, contaboCfg, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig returned unexpected error: %v", err)
	}

	if reaperCfg.ReleaseWindow != 48*time.Hour {
		t.Errorf("ReleaseWindow = %s, want 48h", reaperCfg.ReleaseWindow)
	}
	if reaperCfg.BillingPeriod != 360*time.Hour {
		t.Errorf("BillingPeriod = %s, want 360h", reaperCfg.BillingPeriod)
	}
	if reaperCfg.ElasticTag != "custom-tag" {
		t.Errorf("ElasticTag = %q, want custom-tag", reaperCfg.ElasticTag)
	}
	if contaboCfg.ClientID != "client-id" {
		t.Errorf("ClientID = %q, want client-id", contaboCfg.ClientID)
	}
	if contaboCfg.BaseURL != "https://api.example.com" {
		t.Errorf("BaseURL = %q, want https://api.example.com", contaboCfg.BaseURL)
	}
	if contaboCfg.AuthURL != "https://auth.example.com/token" {
		t.Errorf("AuthURL = %q, want https://auth.example.com/token", contaboCfg.AuthURL)
	}
}

func TestLoadConfig_Defaults(t *testing.T) {
	env := fullEnv()
	reaperCfg, contaboCfg, err := loadConfig(getenvFromMap(env))
	if err != nil {
		t.Fatalf("loadConfig returned unexpected error: %v", err)
	}

	if reaperCfg.ReleaseWindow != defaultReleaseWindow {
		t.Errorf("ReleaseWindow = %s, want default %s", reaperCfg.ReleaseWindow, defaultReleaseWindow)
	}
	if reaperCfg.BillingPeriod != defaultBillingPeriod {
		t.Errorf("BillingPeriod = %s, want default %s", reaperCfg.BillingPeriod, defaultBillingPeriod)
	}
	if reaperCfg.ElasticTag != defaultElasticTag {
		t.Errorf("ElasticTag = %q, want default %q", reaperCfg.ElasticTag, defaultElasticTag)
	}
	if contaboCfg.BaseURL != defaultContaboBaseURL {
		t.Errorf("BaseURL = %q, want default %q", contaboCfg.BaseURL, defaultContaboBaseURL)
	}
	if contaboCfg.AuthURL != defaultContaboAuthURL {
		t.Errorf("AuthURL = %q, want default %q", contaboCfg.AuthURL, defaultContaboAuthURL)
	}
}

func TestLoadConfig_MissingRequiredVar(t *testing.T) {
	for _, missing := range []string{"CONTABO_CLIENT_ID", "CONTABO_CLIENT_SECRET", "CONTABO_API_USER", "CONTABO_API_PASSWORD"} {
		t.Run(missing, func(t *testing.T) {
			env := fullEnv()
			delete(env, missing)
			if _, _, err := loadConfig(getenvFromMap(env)); err == nil {
				t.Fatalf("expected an error when %s is missing", missing)
			}
		})
	}
}

func TestLoadConfig_InvalidDurationErrors(t *testing.T) {
	env := fullEnv()
	env["RELEASE_WINDOW"] = "not-a-duration"
	if _, _, err := loadConfig(getenvFromMap(env)); err == nil {
		t.Fatal("expected an error for an invalid RELEASE_WINDOW")
	}

	env2 := fullEnv()
	env2["BILLING_PERIOD"] = "not-a-duration"
	if _, _, err := loadConfig(getenvFromMap(env2)); err == nil {
		t.Fatal("expected an error for an invalid BILLING_PERIOD")
	}
}
