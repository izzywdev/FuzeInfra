package provider

import (
	"context"
	"fmt"
	"strconv"
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

	// Compute next available index for naming.
	// Find the max numeric suffix among existing elastic instance names,
	// then increment to get the start index for new instances.
	nextIndex := computeNextIndex(instances, s.cfg.NamePrefix)

	// Create Delta instances.
	for i := 0; i < int(req.Delta); i++ {
		// Build instance name
		instanceName := fmt.Sprintf("%s-%d", s.cfg.NamePrefix, nextIndex+i)

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

// computeNextIndex finds the max numeric suffix in existing instance names
// matching the NamePrefix pattern and returns the next index to use.
// If no matching instances, returns 0.
func computeNextIndex(instances []contabo.Instance, namePrefix string) int {
	prefix := namePrefix + "-"
	maxIdx := -1

	for _, inst := range instances {
		// Check if the name starts with the expected prefix
		if !strings.HasPrefix(inst.Name, prefix) {
			continue
		}

		// Extract the suffix after the prefix
		suffix := inst.Name[len(prefix):]

		// Try to parse the suffix as an integer
		idx, err := strconv.Atoi(suffix)
		if err != nil {
			// Suffix is not a valid integer; skip this instance
			continue
		}

		if idx > maxIdx {
			maxIdx = idx
		}
	}

	if maxIdx < 0 {
		return 0
	}
	return maxIdx + 1
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
