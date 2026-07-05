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
	"syscall"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
	"google.golang.org/grpc"
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
//     Not passed to the Contabo API directly — see USER_DATA_TEMPLATE_B64 below
//     for how it reaches the created node.
//   - K3S_NODE_TOKEN          (required) k3s node join token. Same caveat as
//     K3S_SERVER_URL above.
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
//   - SSH_KEY_ID              (required) Contabo SSH key ID injected into new
//     instances.
//   - GRPC_LISTEN             (optional) gRPC server listen address.
//     Default: :8086
//   - USER_DATA_TEMPLATE_B64  (optional) Base64-encoded Go text/template used
//     as cloud-init UserData for new instances. The ONLY template variable
//     the provider substitutes is {{.NodeName}} (see internal/provider's
//     renderUserData). This binary does NOT string-replace K3S_SERVER_URL or
//     K3S_NODE_TOKEN into the template — if the cloud-init needs those
//     values, the operator must bake the literal values into the template
//     text before base64-encoding it (K3S_SERVER_URL / K3S_NODE_TOKEN are
//     read here only for startup validation, confirming the operator has
//     configured a k3s join target, not for template substitution). If
//     USER_DATA_TEMPLATE_B64 is empty, an empty UserData is used.
const (
	defaultContaboBaseURL = "https://api.contabo.com"
	defaultContaboAuthURL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
	defaultElasticTag     = "fuzeinfra-elastic"
	defaultNamePrefix     = "fuzeinfra-elastic"
	defaultMinSize        = 0
	defaultMaxSize        = 2
	defaultGRPCListen     = ":8086"
)

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

	// Required string fields.
	required := map[string]string{
		"CONTABO_CLIENT_ID":     contaboClientID,
		"CONTABO_CLIENT_SECRET": contaboClientSecret,
		"CONTABO_API_USER":      contaboAPIUser,
		"CONTABO_API_PASSWORD":  contaboAPIPassword,
		"K3S_SERVER_URL":        k3sServerURL,
		"K3S_NODE_TOKEN":        k3sNodeToken,
		"PRODUCT_ID":            productID,
		"IMAGE_ID":              imageID,
		"REGION":                region,
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

	// SSH_KEY_ID: required, parsed as int64.
	sshKeyIDStr := get("SSH_KEY_ID")
	if sshKeyIDStr == "" {
		return provider.Config{}, contabo.Config{}, "", fmt.Errorf("missing required environment variable SSH_KEY_ID")
	}
	sshKeyID, err := strconv.ParseInt(sshKeyIDStr, 10, 64)
	if err != nil {
		return provider.Config{}, contabo.Config{}, "", fmt.Errorf("parsing SSH_KEY_ID=%q: %w", sshKeyIDStr, err)
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

	client := contabo.NewClient(contaboCfg)
	srv := provider.New(provCfg, client)

	lis, err := net.Listen("tcp", grpcListen)
	if err != nil {
		log.Fatalf("failed to listen on %s: %v", grpcListen, err)
	}

	grpcServer := grpc.NewServer()
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
