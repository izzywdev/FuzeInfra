// Command reaper is the billing-aware reaper for FuzeInfra's Contabo elastic
// node pool. It runs ONCE and exits — it is deployed as an hourly Kubernetes
// CronJob (helm/fuzeinfra/templates/autoscaler/reaper.yaml), not a daemon.
//
// See internal/reaper for the full rationale and decision logic: Contabo
// only terminates a canceled instance at the END of its current monthly
// billing period, so cordoning/draining an elastic node early (the normal
// Cluster Autoscaler scale-down behavior) wastes the rest of the paid month.
// This binary instead releases (cordons, drains, cancels) an elastic
// instance only once it is within RELEASE_WINDOW of its projected monthly
// renewal AND currently idle.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/reaper"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// Environment variables consumed by this binary:
//
//   - RELEASE_WINDOW        (optional) Go duration string; how far ahead of
//     an instance's projected monthly renewal the reaper will consider
//     releasing it, if idle. Default: 24h.
//   - BILLING_PERIOD        (optional) Go duration string; the length of
//     Contabo's monthly billing cycle used to project the next renewal from
//     an instance's CreatedDate. Default: 720h (~30d).
//   - ELASTIC_TAG           (optional) Contabo tag identifying elastic
//     instances. Default: fuzeinfra-elastic.
//   - CONTABO_CLIENT_ID     (required) Contabo OAuth2 client ID.
//   - CONTABO_CLIENT_SECRET (required) Contabo OAuth2 client secret.
//   - CONTABO_API_USER      (required) Contabo API username.
//   - CONTABO_API_PASSWORD  (required) Contabo API password.
//   - CONTABO_BASE_URL      (optional) Contabo REST API base URL.
//     Default: https://api.contabo.com
//   - CONTABO_AUTH_URL      (optional) Contabo OAuth2 token endpoint.
//     Default: https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token
//
// The Kubernetes client is always in-cluster (rest.InClusterConfig()) — this
// binary is only ever run as a CronJob pod via its own ServiceAccount
// (fuzeinfra-ca-reaper), never locally against an out-of-cluster kubeconfig.
const (
	defaultReleaseWindow  = 24 * time.Hour
	defaultBillingPeriod  = 720 * time.Hour
	defaultElasticTag     = "fuzeinfra-elastic"
	defaultContaboBaseURL = "https://api.contabo.com"
	defaultContaboAuthURL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
)

func loadConfig(getenv func(string) string) (reaper.Config, contabo.Config, error) {
	get := func(key string) string { return getenv(key) }
	getDefault := func(key, def string) string {
		if v := getenv(key); v != "" {
			return v
		}
		return def
	}

	releaseWindow := defaultReleaseWindow
	if v := get("RELEASE_WINDOW"); v != "" {
		parsed, err := time.ParseDuration(v)
		if err != nil {
			return reaper.Config{}, contabo.Config{}, fmt.Errorf("parsing RELEASE_WINDOW=%q: %w", v, err)
		}
		releaseWindow = parsed
	}

	billingPeriod := defaultBillingPeriod
	if v := get("BILLING_PERIOD"); v != "" {
		parsed, err := time.ParseDuration(v)
		if err != nil {
			return reaper.Config{}, contabo.Config{}, fmt.Errorf("parsing BILLING_PERIOD=%q: %w", v, err)
		}
		billingPeriod = parsed
	}

	contaboClientID := get("CONTABO_CLIENT_ID")
	contaboClientSecret := get("CONTABO_CLIENT_SECRET")
	contaboAPIUser := get("CONTABO_API_USER")
	contaboAPIPassword := get("CONTABO_API_PASSWORD")

	required := map[string]string{
		"CONTABO_CLIENT_ID":     contaboClientID,
		"CONTABO_CLIENT_SECRET": contaboClientSecret,
		"CONTABO_API_USER":      contaboAPIUser,
		"CONTABO_API_PASSWORD":  contaboAPIPassword,
	}
	for name, val := range required {
		if val == "" {
			return reaper.Config{}, contabo.Config{}, fmt.Errorf("missing required environment variable %s", name)
		}
	}

	reaperCfg := reaper.Config{
		ReleaseWindow:   releaseWindow,
		BillingPeriod:   billingPeriod,
		ElasticTag:      getDefault("ELASTIC_TAG", defaultElasticTag),
		EvictionTimeout: 60 * time.Second,
	}

	contaboCfg := contabo.Config{
		ClientID:     contaboClientID,
		ClientSecret: contaboClientSecret,
		User:         contaboAPIUser,
		Pass:         contaboAPIPassword,
		BaseURL:      getDefault("CONTABO_BASE_URL", defaultContaboBaseURL),
		AuthURL:      getDefault("CONTABO_AUTH_URL", defaultContaboAuthURL),
	}

	return reaperCfg, contaboCfg, nil
}

func main() {
	reaperCfg, contaboCfg, err := loadConfig(os.Getenv)
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	inClusterCfg, err := rest.InClusterConfig()
	if err != nil {
		log.Fatalf("failed to load in-cluster Kubernetes config: %v", err)
	}
	k8sClient, err := kubernetes.NewForConfig(inClusterCfg)
	if err != nil {
		log.Fatalf("failed to build Kubernetes client: %v", err)
	}

	contaboClient := contabo.NewClient(contaboCfg)

	r := reaper.New(contaboClient, k8sClient, reaperCfg)

	log.Printf("reaper: starting one-shot pass (elastic tag=%q, releaseWindow=%s, billingPeriod=%s)",
		reaperCfg.ElasticTag, reaperCfg.ReleaseWindow, reaperCfg.BillingPeriod)

	// A CronJob pod has no external deadline signal of its own beyond the
	// job's activeDeadlineSeconds (enforced by Kubernetes, not this
	// process); a generous local timeout still bounds a single stuck HTTP
	// call from hanging the pod forever between CronJob-level kills.
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()

	summary, err := r.Run(ctx)
	if err != nil {
		log.Fatalf("reaper: run failed: %v", err)
	}

	log.Printf("reaper: done. checked=%d released=%d kept=%d errors=%d",
		summary.Checked, summary.Released, summary.Kept, summary.Errors)

	if summary.Errors > 0 {
		// Nonzero exit surfaces a CronJob failure in kubectl/monitoring even
		// though individual instance errors don't abort the pass itself.
		os.Exit(1)
	}
}
