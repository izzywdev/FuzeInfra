package provider

import (
	"context"
	"crypto/rand"
	"fmt"
	"strings"
	"text/template"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// NodeGroupIncreaseSize creates new elastic Contabo VPS nodes.
// It enforces a hard maximum node count via cfg.MaxSize as a safety cap
// against runaway autoscaler scaling.
//
// Returns codes.InvalidArgument if Delta <= 0.
// Returns codes.OutOfRange if the requested size would exceed MaxSize.
// Propagates ListByTag or Create errors.
func (s *Server) NodeGroupIncreaseSize(ctx context.Context, req *protos.NodeGroupIncreaseSizeRequest) (*protos.NodeGroupIncreaseSizeResponse, error) {
	// Validate Delta
	if req.Delta <= 0 {
		return nil, status.Errorf(codes.InvalidArgument, "Delta must be positive, got %d", req.Delta)
	}

	// Fetch current elastic instances to check cap
	instances, err := s.cloud.ListByTag(ctx, s.cfg.ElasticTag)
	if err != nil {
		return nil, status.Errorf(codes.Unavailable, "NodeGroupIncreaseSize: listing elastic instances: %v", err)
	}

	current := len(instances)
	requested := current + int(req.Delta)

	// Enforce hard cap
	if requested > s.cfg.MaxSize {
		return nil, status.Errorf(codes.OutOfRange,
			"would exceed MaxSize: current=%d, Delta=%d, MaxSize=%d",
			current, req.Delta, s.cfg.MaxSize)
	}

	// Track names already known (from the tagged fleet) or freshly minted in
	// this loop, so a random-suffix collision is retried locally rather than
	// sent to Contabo (which 400s on a duplicate display name).
	usedNames := make(map[string]struct{}, len(instances)+int(req.Delta))
	for _, inst := range instances {
		usedNames[inst.Name] = struct{}{}
	}

	// Create Delta instances.
	for i := 0; i < int(req.Delta); i++ {
		// Build a unique instance name.
		instanceName, err := uniqueInstanceName(s.cfg.NamePrefix, usedNames)
		if err != nil {
			return nil, status.Errorf(codes.Internal, "NodeGroupIncreaseSize: generating instance name: %v", err)
		}
		usedNames[instanceName] = struct{}{}

		// Render UserData from template
		userData, err := renderUserData(s.cfg.UserDataTmpl, instanceName, s.cfg.K3SServerURL, s.cfg.K3SNodeToken)
		if err != nil {
			return nil, fmt.Errorf("NodeGroupIncreaseSize: rendering UserData for %q: %w", instanceName, err)
		}

		// Create the instance
		_, err = s.cloud.Create(ctx, contabo.CreateReq{
			Name:      instanceName,
			ProductID: s.cfg.ProductID,
			ImageID:   s.cfg.ImageID,
			Region:    s.cfg.Region,
			SSHKeyID:  s.cfg.SSHKeyID,
			UserData:  userData,
			Tags:      []string{s.cfg.ElasticTag},
		})
		if err != nil {
			return nil, status.Errorf(codes.Unavailable, "NodeGroupIncreaseSize: creating %q: %v", instanceName, err)
		}
	}

	return &protos.NodeGroupIncreaseSizeResponse{}, nil
}

// maxNameSuffixAttempts bounds the retry loop in uniqueInstanceName against a
// pathological run of random collisions. At 4 random bytes (32 bits) of
// suffix space, exhausting this many attempts in practice indicates a bug
// (e.g. a broken RNG) rather than bad luck.
const maxNameSuffixAttempts = 20

// uniqueInstanceName generates an instance name of the form
// "<namePrefix>-<8-hex-suffix>" that is not already present in used.
//
// The suffix is a short random value (crypto/rand, mirroring the UUID
// request-id helper in internal/contabo/client.go) rather than a sequential
// index. A sequential index collides whenever a prior create left an
// untracked or still-cancelling instance holding a name: Contabo's cancel is
// end-of-billing, so a cancelled instance keeps its display name (and thus
// blocks reuse of that name) for the remainder of the billing period, and
// Contabo's create API 400s ("There is already an instance with the same
// display name") on any collision. The prefix invariant is preserved and the
// resulting name still flows unmodified through create -> tag -> cloud-init
// --node-name -> providerID, which is all NodeGroupForNode/
// NodeGroupDeleteNodes require for correlation — nothing depends on the
// suffix being sequential or predictable, only unique.
func uniqueInstanceName(namePrefix string, used map[string]struct{}) (string, error) {
	for attempt := 0; attempt < maxNameSuffixAttempts; attempt++ {
		suffix, err := randomHexSuffix()
		if err != nil {
			return "", err
		}
		name := fmt.Sprintf("%s-%s", namePrefix, suffix)
		if _, collision := used[name]; !collision {
			return name, nil
		}
	}
	return "", fmt.Errorf("could not generate a unique instance name after %d attempts", maxNameSuffixAttempts)
}

// randomHexSuffix returns 4 crypto/rand bytes rendered as 8 lowercase hex
// characters, e.g. "a1b2c3d4".
func randomHexSuffix() (string, error) {
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("generating random name suffix: %w", err)
	}
	return fmt.Sprintf("%x", b), nil
}

// renderUserData renders cfg.UserDataTmpl with the provided nodeName and the
// k3s join parameters. If cfg.UserDataTmpl is empty, returns empty string.
// The template receives a struct with .NodeName, .K3SServerURL, and
// .K3SNodeToken fields, so a cloud-init template can reference the k3s join
// target (e.g. {{.K3SServerURL}} / {{.K3SNodeToken}}) without the operator
// hand-duplicating the literal values into the template text.
func renderUserData(templateStr, nodeName, k3sServerURL, k3sNodeToken string) (string, error) {
	if templateStr == "" {
		return "", nil
	}

	tmpl, err := template.New("userdata").Parse(templateStr)
	if err != nil {
		return "", fmt.Errorf("parsing UserDataTmpl: %w", err)
	}

	data := struct {
		NodeName     string
		K3SServerURL string
		K3SNodeToken string
	}{
		NodeName:     nodeName,
		K3SServerURL: k3sServerURL,
		K3SNodeToken: k3sNodeToken,
	}

	var buf strings.Builder
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("executing UserDataTmpl: %w", err)
	}

	return buf.String(), nil
}

// NodeGroupDeleteNodes deletes nodes from the elastic node group.
// It enforces a critical safety guard: it will NEVER delete a non-elastic instance.
// The requested nodes are validated against the elastic set via ListByTag BEFORE any deletion occurs.
//
// ProviderIDs are name-based (contabo://<node-name>), matching what NodeGroupNodes
// returns and what cloud-init sets via --kubelet-arg=provider-id at join time (see
// deploy/elastic-userdata.template). The numeric Contabo instance id needed for the
// actual Delete call is resolved by looking up the parsed name in the ListByTag
// result, which is fetched once and reused for both the membership check and the
// name->id resolution.
//
// Returns codes.InvalidArgument if any requested node is not in the elastic set
// (including a malformed ProviderID or a name that resolves to no known instance).
// Returns codes.Unavailable if ListByTag or Delete fails.
func (s *Server) NodeGroupDeleteNodes(ctx context.Context, req *protos.NodeGroupDeleteNodesRequest) (*protos.NodeGroupDeleteNodesResponse, error) {
	// Fetch current elastic instances to build the allowed-to-delete set
	// and the name->instance lookup used to resolve the numeric Contabo id.
	elasticInstances, err := s.cloud.ListByTag(ctx, s.cfg.ElasticTag)
	if err != nil {
		return nil, status.Errorf(codes.Unavailable, "NodeGroupDeleteNodes: listing elastic instances: %v", err)
	}

	// Build a name -> instance map for membership checking and id resolution.
	elasticByName := make(map[string]contabo.Instance, len(elasticInstances))
	for _, inst := range elasticInstances {
		elasticByName[inst.Name] = inst
	}

	// Validate ALL requested nodes are elastic BEFORE deleting any (fail-closed guard).
	// Resolve the numeric ids up front so the delete loop below cannot fail this
	// lookup after already having deleted some nodes.
	ids := make([]int64, len(req.Nodes))
	for i, node := range req.Nodes {
		name, err := parseContaboNodeName(node.ProviderID)
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "NodeGroupDeleteNodes: parsing ProviderID %q: %v", node.ProviderID, err)
		}

		inst, ok := elasticByName[name]
		if !ok {
			return nil, status.Errorf(codes.InvalidArgument, "refusing to delete non-elastic node %s", node.ProviderID)
		}

		ids[i] = inst.ID
	}

	// All nodes validated as elastic; now delete them by their resolved numeric id.
	for i, node := range req.Nodes {
		if err := s.cloud.Delete(ctx, ids[i]); err != nil {
			return nil, status.Errorf(codes.Unavailable, "NodeGroupDeleteNodes: deleting node %s: %v", node.ProviderID, err)
		}
	}

	return &protos.NodeGroupDeleteNodesResponse{}, nil
}

// NodeGroupDecreaseTargetSize is a no-op for Contabo.
// Contabo has no reservation or target size concept; the infrastructure is stateless.
func (s *Server) NodeGroupDecreaseTargetSize(ctx context.Context, req *protos.NodeGroupDecreaseTargetSizeRequest) (*protos.NodeGroupDecreaseTargetSizeResponse, error) {
	return &protos.NodeGroupDecreaseTargetSizeResponse{}, nil
}

// parseContaboNodeName parses a ProviderID in the format "contabo://<name>" and
// returns the node name suffix. The scheme is name-based (see NodeGroupNodes in
// size.go for why), so this returns a string rather than a numeric id.
func parseContaboNodeName(providerID string) (string, error) {
	const prefix = "contabo://"
	if !strings.HasPrefix(providerID, prefix) {
		return "", fmt.Errorf("ProviderID does not start with %q", prefix)
	}

	name := providerID[len(prefix):]
	if name == "" {
		return "", fmt.Errorf("ProviderID %q has empty name suffix", providerID)
	}

	return name, nil
}
