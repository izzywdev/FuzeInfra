// Command server runs the Contabo externalgrpc Cluster Autoscaler cloud
// provider as a standalone gRPC server.
//
// Configuration is entirely env-driven so the binary can be deployed as a
// container sidecar to Cluster Autoscaler with no config files. See
// loadConfig for the full list of environment variables and their defaults.
package main

import (
	"context"
	"encoding/base64"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/notify"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

// Environment variables consumed by this binary:
//
//   - CONTABO_CLIENT_ID       (required) Contabo OAuth2 client ID.
//   - CONTABO_CLIENT_SECRET   (required) Contabo OAuth2 client secret.
//   - CONTABO_API_USER        (required) Contabo API username.
//   - CONTABO_API_PASSWORD    (required) Contabo API password.
//   - CONTABO_BASE_URL        (optional) Contabo REST API base URL.
//     Default: https://api.contabo.com
//   - CONTABO_AUTH_URL        (optional) Contabo OAuth2 token endpoint.
//     Default: https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token
//   - K3S_SERVER_URL          (required) k3s server URL new elastic nodes join.
//     Not passed to the Contabo API directly — it is exposed to the cloud-init
//     template as {{.K3SServerURL}}; see USER_DATA_TEMPLATE_B64 below.
//   - K3S_NODE_TOKEN          (required) k3s node join token. Exposed to the
//     cloud-init template as {{.K3SNodeToken}}. Same caveat as K3S_SERVER_URL.
//   - ELASTIC_TAG             (optional) Tag applied to elastic instances.
//     Default: fuzeinfra-elastic
//   - ELASTIC_NAME_PREFIX     (optional) Prefix for elastic instance names.
//     Default: fuzeinfra-elastic
//   - PRODUCT_ID              (required) Contabo product SKU for new nodes.
//   - IMAGE_ID                (required) Contabo OS image ID for new nodes.
//   - REGION                  (required) Contabo data-centre region.
//   - MIN_SIZE                (optional) Minimum elastic node group size.
//     Default: 0
//   - MAX_SIZE                (optional) Maximum elastic node group size
//     (hard cap enforced by the provider). Default: 2
//   - SSH_KEY_ID              (optional) Contabo secrets-API SSH key ID to
//     register on new instances via the sshKeys field. Default: 0 (unset).
//     The FuzeInfra Contabo account has zero registered SSH-key secrets, so
//     this is normally left unset — internal/contabo/client.go's Create()
//     omits "sshKeys" from the create request entirely whenever SSHKeyID <=
//     0. Break-glass SSH access is instead provisioned via cloud-init (see
//     USER_DATA_TEMPLATE_B64 below / deploy/elastic-userdata.template).
//   - GRPC_LISTEN             (optional) gRPC server listen address.
//     Default: :8086
//   - USER_DATA_TEMPLATE_B64  (optional) Base64-encoded Go text/template used
//     as cloud-init UserData for new instances. The template is rendered per
//     node with three variables (see internal/provider's renderUserData):
//     {{.NodeName}}, {{.K3SServerURL}} (from K3S_SERVER_URL), and
//     {{.K3SNodeToken}} (from K3S_NODE_TOKEN). A cloud-init join command can
//     therefore reference the k3s join target directly instead of the operator
//     hand-duplicating the literal values into the template text. If
//     USER_DATA_TEMPLATE_B64 is empty, an empty UserData is used.
//   - NOTIFY_EMAIL_ENABLED    (optional) When "1" or "true", NodeGroupIncreaseSize
//     sends a best-effort email immediately before every Create call (see
//     internal/notify and internal/provider/scale.go). Default: false (disabled) —
//     this is a purely informational early-warning, never the authoritative
//     safeguard (that's the MaxSize name-prefix cap enforced regardless).
//   - NOTIFY_SMTP_HOST        (required if NOTIFY_EMAIL_ENABLED) SMTP relay host.
//   - NOTIFY_SMTP_PORT        (optional) SMTP relay port. Default: 587.
//   - NOTIFY_SMTP_USER        (optional) SMTP auth username; if empty, no SMTP
//     AUTH is attempted (some relays accept unauthenticated mail).
//   - NOTIFY_SMTP_PASS        (optional) SMTP auth password. Source via the
//     provider's existingSecret SealedSecret, never a plain Helm value.
//   - NOTIFY_EMAIL_FROM       (required if NOTIFY_EMAIL_ENABLED) envelope/header From address.
//   - NOTIFY_EMAIL_TO         (required if NOTIFY_EMAIL_ENABLED) comma-separated recipient list.
//   - NOTIFY_TELEGRAM_ENABLED    (optional) When "1" or "true", NodeGroupIncreaseSize
//     sends the same best-effort before-provision warning via the Telegram Bot API
//     instead of email (see internal/notify.Telegram). Default: false. When both
//     NOTIFY_TELEGRAM_ENABLED and NOTIFY_EMAIL_ENABLED are set, Telegram takes
//     precedence — only one notifier is ever wired up.
//   - NOTIFY_TELEGRAM_BOT_TOKEN  (required if NOTIFY_TELEGRAM_ENABLED) Telegram bot
//     token issued by @BotFather. Source via the provider's existingSecret
//     SealedSecret, never a plain Helm value.
//   - NOTIFY_TELEGRAM_CHAT_ID    (required if NOTIFY_TELEGRAM_ENABLED) destination
//     chat id the warning is posted to.
//   - FAKE_CLOUD              (optional) When "1" or "true", the server backs
//     the provider with an in-memory fake (internal/contabo.MemClient)
//     instead of the real Contabo HTTP API, and CONTABO_*/K3S_* credentials
//     are NOT required. This exists solely to exercise the CA-to-provider
//     gRPC loop end-to-end (e.g. under kind) without touching real Contabo
//     infrastructure — see test/integration/kind-autoscaling-test.sh. The
//     provider config fields that describe *what* to create (PRODUCT_ID,
//     IMAGE_ID, REGION, MIN_SIZE, MAX_SIZE) are still required and validated
//     identically in fake mode, since NodeGroupIncreaseSize/DeleteNodes logic
//     is exercised unchanged. Default: false (real Contabo mode).
const (
	defaultContaboBaseURL = "https://api.contabo.com"
	defaultContaboAuthURL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
	defaultElasticTag     = "fuzeinfra-elastic"
	defaultNamePrefix     = "fuzeinfra-elastic"
	defaultMinSize        = 0
	defaultMaxSize        = 2
	defaultGRPCListen     = ":8086"
	defaultNotifySMTPPort = "587"
)

// envFlagTrue parses a boolean-flag-shaped env var value: "1" or "true"
// (case-insensitive) is true; anything else (including unset/empty) is
// false. Shared by every "1"/"true" boolean env var in this binary
// (FAKE_CLOUD, NOTIFY_EMAIL_ENABLED, NOTIFY_TELEGRAM_ENABLED) so they all
// accept the same convention.
func envFlagTrue(v string) bool {
	return v == "1" || strings.EqualFold(v, "true")
}

// isFakeCloud reports whether FAKE_CLOUD selects the in-memory fake provider
// backend — see envFlagTrue for the accepted value convention. Any other
// value (including unset/empty) means real Contabo mode.
func isFakeCloud(v string) bool {
	return envFlagTrue(v)
}

// loadConfig builds the provider and contabo configs from environment
// variables obtained via getenv (injected for testability). It also returns
// the resolved gRPC listen address. Returns an error if required variables
// are missing or if numeric/logical validation fails; the caller is expected
// to log.Fatal on error so a misconfigured autoscaler fails fast instead of
// starting and mis-scaling.
func loadConfig(getenv func(string) string) (provider.Config, contabo.Config, string, error) {
	get := func(key string) string { return getenv(key) }
	getDefault := func(key, def string) string {
		if v := getenv(key); v != "" {
			return v
		}
		return def
	}

	fakeCloud := isFakeCloud(get("FAKE_CLOUD"))

	contaboClientID := get("CONTABO_CLIENT_ID")
	contaboClientSecret := get("CONTABO_CLIENT_SECRET")
	contaboAPIUser := get("CONTABO_API_USER")
	contaboAPIPassword := get("CONTABO_API_PASSWORD")
	contaboBaseURL := getDefault("CONTABO_BASE_URL", defaultContaboBaseURL)
	contaboAuthURL := getDefault("CONTABO_AUTH_URL", defaultContaboAuthURL)

	k3sServerURL := get("K3S_SERVER_URL")
	k3sNodeToken := get("K3S_NODE_TOKEN")

	elasticTag := getDefault("ELASTIC_TAG", defaultElasticTag)
	elasticNamePrefix := getDefault("ELASTIC_NAME_PREFIX", defaultNamePrefix)

	productID := get("PRODUCT_ID")
	imageID := get("IMAGE_ID")
	region := get("REGION")

	grpcListen := getDefault("GRPC_LISTEN", defaultGRPCListen)

	// Required string fields. The provider config (what to create) is always
	// required, fake mode or not — NodeGroupIncreaseSize/DeleteNodes logic
	// runs unchanged either way. Contabo/K3S credentials are only required in
	// real mode; FAKE_CLOUD relaxes those so the CA-to-provider loop can be
	// exercised (e.g. under kind) with no real Contabo account.
	required := map[string]string{
		"PRODUCT_ID": productID,
		"IMAGE_ID":   imageID,
		"REGION":     region,
	}
	if !fakeCloud {
		required["CONTABO_CLIENT_ID"] = contaboClientID
		required["CONTABO_CLIENT_SECRET"] = contaboClientSecret
		required["CONTABO_API_USER"] = contaboAPIUser
		required["CONTABO_API_PASSWORD"] = contaboAPIPassword
		required["K3S_SERVER_URL"] = k3sServerURL
		required["K3S_NODE_TOKEN"] = k3sNodeToken
	}
	for name, val := range required {
		if val == "" {
			return provider.Config{}, contabo.Config{}, "", fmt.Errorf("missing required environment variable %s", name)
		}
	}

	// MIN_SIZE / MAX_SIZE: parse with defaults.
	minSize := defaultMinSize
	if v := get("MIN_SIZE"); v != "" {
		parsed, err := strconv.Atoi(v)
		if err != nil {
			return provider.Config{}, contabo.Config{}, "", fmt.Errorf("parsing MIN_SIZE=%q: %w", v, err)
		}
		minSize = parsed
	}

	maxSize := defaultMaxSize
	if v := get("MAX_SIZE"); v != "" {
		parsed, err := strconv.Atoi(v)
		if err != nil {
			return provider.Config{}, contabo.Config{}, "", fmt.Errorf("parsing MAX_SIZE=%q: %w", v, err)
		}
		maxSize = parsed
	}

	// SSH_KEY_ID: optional, parsed as int64, defaults to 0 (unset). A 0/unset
	// value means the created instance references no registered Contabo
	// SSH-key secret at all — internal/contabo/client.go's Create() omits
	// "sshKeys" from the request body in that case. See the SSH_KEY_ID doc
	// comment above for why this is now optional.
	var sshKeyID int64
	if sshKeyIDStr := get("SSH_KEY_ID"); sshKeyIDStr != "" {
		parsed, err := strconv.ParseInt(sshKeyIDStr, 10, 64)
		if err != nil {
			return provider.Config{}, contabo.Config{}, "", fmt.Errorf("parsing SSH_KEY_ID=%q: %w", sshKeyIDStr, err)
		}
		sshKeyID = parsed
	}

	// Validate size bounds: a misconfigured autoscaler must fail fast.
	if maxSize < 1 {
		return provider.Config{}, contabo.Config{}, "", fmt.Errorf("MAX_SIZE must be >= 1, got %d", maxSize)
	}
	if minSize < 0 {
		return provider.Config{}, contabo.Config{}, "", fmt.Errorf("MIN_SIZE must be >= 0, got %d", minSize)
	}
	if minSize > maxSize {
		return provider.Config{}, contabo.Config{}, "", fmt.Errorf("MIN_SIZE (%d) must be <= MAX_SIZE (%d)", minSize, maxSize)
	}

	// USER_DATA_TEMPLATE_B64 is optional; base64-decode if present. The
	// decoded template text is passed through verbatim to provider.Config —
	// the provider renders only {{.NodeName}}; no other substitution happens
	// here or in the provider.
	userDataTmpl := ""
	if v := get("USER_DATA_TEMPLATE_B64"); v != "" {
		decoded, err := base64.StdEncoding.DecodeString(v)
		if err != nil {
			return provider.Config{}, contabo.Config{}, "", fmt.Errorf("decoding USER_DATA_TEMPLATE_B64: %w", err)
		}
		userDataTmpl = string(decoded)
	}

	// NOTIFY_*: optional best-effort before-provision warning, off by default.
	// Two delivery mechanisms are supported — email (NOTIFY_EMAIL_ENABLED)
	// and Telegram (NOTIFY_TELEGRAM_ENABLED) — and at most one notifier is
	// ever wired onto provCfg.Notifier; Telegram takes precedence when both
	// are enabled. When either is set, its delivery-critical fields must be
	// present too — a misconfigured notifier should fail fast at startup
	// rather than silently never send anything (Notify itself is already
	// failure-tolerant per-call; this is a separate, one-time startup check).
	notifyEnabled := envFlagTrue(get("NOTIFY_EMAIL_ENABLED"))
	telegramEnabled := envFlagTrue(get("NOTIFY_TELEGRAM_ENABLED"))
	notifyCfg := notify.Config{
		Enabled:  notifyEnabled,
		SMTPHost: get("NOTIFY_SMTP_HOST"),
		SMTPPort: getDefault("NOTIFY_SMTP_PORT", defaultNotifySMTPPort),
		SMTPUser: get("NOTIFY_SMTP_USER"),
		SMTPPass: get("NOTIFY_SMTP_PASS"),
		From:     get("NOTIFY_EMAIL_FROM"),
		To:       get("NOTIFY_EMAIL_TO"),

		TelegramEnabled:  telegramEnabled,
		TelegramBotToken: get("NOTIFY_TELEGRAM_BOT_TOKEN"),
		TelegramChatID:   get("NOTIFY_TELEGRAM_CHAT_ID"),
	}
	if notifyEnabled {
		notifyRequired := map[string]string{
			"NOTIFY_SMTP_HOST":  notifyCfg.SMTPHost,
			"NOTIFY_EMAIL_FROM": notifyCfg.From,
			"NOTIFY_EMAIL_TO":   notifyCfg.To,
		}
		for name, val := range notifyRequired {
			if val == "" {
				return provider.Config{}, contabo.Config{}, "", fmt.Errorf("missing required environment variable %s (required when NOTIFY_EMAIL_ENABLED is set)", name)
			}
		}
	}
	if telegramEnabled {
		telegramRequired := map[string]string{
			"NOTIFY_TELEGRAM_BOT_TOKEN": notifyCfg.TelegramBotToken,
			"NOTIFY_TELEGRAM_CHAT_ID":   notifyCfg.TelegramChatID,
		}
		for name, val := range telegramRequired {
			if val == "" {
				return provider.Config{}, contabo.Config{}, "", fmt.Errorf("missing required environment variable %s (required when NOTIFY_TELEGRAM_ENABLED is set)", name)
			}
		}
	}

	// Selection: Telegram takes precedence over email when both are enabled;
	// otherwise fall back to the (possibly disabled, safe no-op) Emailer so
	// provCfg.Notifier is always non-nil.
	var notifier notify.Notifier
	if telegramEnabled {
		notifier = notify.NewTelegram(notifyCfg)
	} else {
		notifier = notify.New(notifyCfg)
	}

	provCfg := provider.Config{
		ElasticTag:   elasticTag,
		NamePrefix:   elasticNamePrefix,
		ProductID:    productID,
		ImageID:      imageID,
		Region:       region,
		MinSize:      minSize,
		MaxSize:      maxSize,
		SSHKeyID:     sshKeyID,
		UserDataTmpl: userDataTmpl,
		K3SServerURL: k3sServerURL,
		K3SNodeToken: k3sNodeToken,
		Notifier:     notifier,
	}

	contaboCfg := contabo.Config{
		ClientID:     contaboClientID,
		ClientSecret: contaboClientSecret,
		User:         contaboAPIUser,
		Pass:         contaboAPIPassword,
		BaseURL:      contaboBaseURL,
		AuthURL:      contaboAuthURL,
	}

	return provCfg, contaboCfg, grpcListen, nil
}

func main() {
	provCfg, contaboCfg, grpcListen, err := loadConfig(os.Getenv)
	if err != nil {
		log.Fatalf("config error: %v", err)
	}

	var client contabo.Client
	if isFakeCloud(os.Getenv("FAKE_CLOUD")) {
		log.Println("FAKE_CLOUD enabled: using in-memory fake Contabo client (no real Contabo API calls will be made)")
		client = contabo.NewMemClient()
	} else {
		client = contabo.NewClient(contaboCfg)
	}
	srv := provider.New(provCfg, client)

	lis, err := net.Listen("tcp", grpcListen)
	if err != nil {
		log.Fatalf("failed to listen on %s: %v", grpcListen, err)
	}

	// TLS is opt-in: set TLS_CERT_FILE + TLS_KEY_FILE to serve gRPC over TLS.
	var serverOpts []grpc.ServerOption
	if certFile, keyFile := os.Getenv("TLS_CERT_FILE"), os.Getenv("TLS_KEY_FILE"); certFile != "" && keyFile != "" {
		creds, err := credentials.NewServerTLSFromFile(certFile, keyFile)
		if err != nil {
			log.Fatalf("failed to load TLS credentials: %v", err)
		}
		serverOpts = append(serverOpts, grpc.Creds(creds))
		log.Println("gRPC server: TLS enabled (TLS_CERT_FILE/TLS_KEY_FILE set)")
	} else {
		// Plaintext is intentional for the default in-cluster deployment: this
		// server is a ClusterIP Service reachable only from the Cluster
		// Autoscaler pod over the pod network (no Ingress/NodePort), and the CA
		// cloud-config dials it plaintext. Set TLS_CERT_FILE/TLS_KEY_FILE to
		// enable TLS; restrict ingress further with a NetworkPolicy.
		log.Println("gRPC server: plaintext (in-cluster ClusterIP only). Set TLS_CERT_FILE/TLS_KEY_FILE to enable TLS.")
	}
	// nosemgrep: grpc-server-insecure-connection
	grpcServer := grpc.NewServer(serverOpts...)
	protos.RegisterCloudProviderServer(grpcServer, srv)

	log.Printf("contabo-externalgrpc server starting on %s (elastic tag=%q, min=%d, max=%d)",
		grpcListen, provCfg.ElasticTag, provCfg.MinSize, provCfg.MaxSize)

	errCh := make(chan error, 1)
	go func() {
		errCh <- grpcServer.Serve(lis)
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	select {
	case <-ctx.Done():
		log.Println("shutdown signal received, stopping gRPC server gracefully")
		grpcServer.GracefulStop()
	case err := <-errCh:
		if err != nil {
			log.Fatalf("gRPC server exited with error: %v", err)
		}
	}
}
