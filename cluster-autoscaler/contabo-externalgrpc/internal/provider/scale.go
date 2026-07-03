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
		userData, err := renderUserData(s.cfg.UserDataTmpl, instanceName)
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

// renderUserData renders cfg.UserDataTmpl with the provided nodeName.
// If cfg.UserDataTmpl is empty, returns empty string.
// The template receives a struct with .NodeName field.
func renderUserData(templateStr, nodeName string) (string, error) {
	if templateStr == "" {
		return "", nil
	}

	tmpl, err := template.New("userdata").Parse(templateStr)
	if err != nil {
		return "", fmt.Errorf("parsing UserDataTmpl: %w", err)
	}

	data := struct {
		NodeName string
	}{
		NodeName: nodeName,
	}

	var buf strings.Builder
	if err := tmpl.Execute(&buf, data); err != nil {
		return "", fmt.Errorf("executing UserDataTmpl: %w", err)
	}

	return buf.String(), nil
}

