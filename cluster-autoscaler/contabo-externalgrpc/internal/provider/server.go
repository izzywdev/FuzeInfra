// Package provider implements the gRPC CloudProvider server for Contabo.
// It vendors the pre-generated protos from kubernetes/autoscaler
// (cluster-autoscaler-release-1.30) and wires them to the Contabo REST client.
package provider

import (
	"context"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/contabo"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/notify"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
)

// Config holds provider-level configuration for the Contabo cloud provider.
type Config struct {
	// ElasticTag is the tag applied to all elastic (autoscaler-managed) instances.
	ElasticTag string
	// NamePrefix is prepended to each created instance name.
	NamePrefix string
	// ProductID is the Contabo product SKU to use when creating nodes.
	ProductID string
	// ImageID is the OS image to use when creating nodes.
	ImageID string
	// Region is the Contabo data-centre region.
	Region string
	// MinSize is the minimum number of nodes the node group will maintain.
	MinSize int
	// MaxSize is the maximum number of nodes the node group may scale to.
	MaxSize int
	// SSHKeyID is the Contabo secrets-API SSH key ID to reference on created
	// instances via the sshKeys field. Optional: 0 (the default when
	// SSH_KEY_ID is unset — see cmd/server/main.go's loadConfig) means no
	// registered SSH-key secret is referenced at all; internal/contabo's
	// Client.Create() omits "sshKeys" from the request entirely in that
	// case. Break-glass SSH access instead comes via cloud-init (UserDataTmpl).
	SSHKeyID int64
	// UserDataTmpl is a Go template string executed at node creation time.
	// See renderUserData for the fields exposed to the template.
	UserDataTmpl string
	// K3SServerURL is the k3s server URL new elastic nodes join. It is exposed
	// to UserDataTmpl as {{.K3SServerURL}} so the cloud-init join command can
	// reference it without the operator hand-duplicating the literal value.
	K3SServerURL string
	// K3SNodeToken is the k3s node join token. It is exposed to UserDataTmpl as
	// {{.K3SNodeToken}}. Same rationale as K3SServerURL.
	K3SNodeToken string
	// PrivateNetworking, when true, makes NodeGroupIncreaseSize order the paid
	// Contabo Private Networking add-on on every created elastic node (so it can
	// join the private VLAN without a separate /upgrade). Sourced from
	// CONTABO_PRIVATE_NETWORKING (see cmd/server/main.go). Default false: turn on
	// only during the coordinated private-VLAN cutover, since a node on
	// private-only flannel cannot join a still-public control plane.
	PrivateNetworking bool
	// Notifier is an optional best-effort email warning sent immediately
	// before each Create call in NodeGroupIncreaseSize (see scale.go). A nil
	// Notifier (the zero value — e.g. NOTIFY_EMAIL_ENABLED unset) is a
	// no-op: the cap/create logic behaves identically either way, since the
	// notifier is purely informational, never authoritative (the prefix-
	// count hard cap is what actually prevents runaway scaling).
	Notifier notify.Notifier
}

// Server is the gRPC CloudProvider server implementation for Contabo.
// It embeds UnimplementedCloudProviderServer so future proto methods that
// we have not yet implemented return codes.Unimplemented instead of panicking.
type Server struct {
	protos.UnimplementedCloudProviderServer

	cfg   Config
	cloud contabo.Client
}

// New returns a new Server backed by the given Contabo client.
func New(cfg Config, cloud contabo.Client) *Server {
	return &Server{cfg: cfg, cloud: cloud}
}

// --- No-op methods (trivial responses; real logic added in later tasks) ---

// Refresh is called before every main loop and can be used to dynamically
// update cloud provider state. No-op for now.
func (s *Server) Refresh(_ context.Context, _ *protos.RefreshRequest) (*protos.RefreshResponse, error) {
	return &protos.RefreshResponse{}, nil
}

// Cleanup cleans up open resources before the cloud provider is destroyed.
// No-op for now.
func (s *Server) Cleanup(_ context.Context, _ *protos.CleanupRequest) (*protos.CleanupResponse, error) {
	return &protos.CleanupResponse{}, nil
}

// GPULabel returns the label added to nodes with GPU resource.
// Contabo nodes have no GPU, so we always return an empty label.
func (s *Server) GPULabel(_ context.Context, _ *protos.GPULabelRequest) (*protos.GPULabelResponse, error) {
	return &protos.GPULabelResponse{}, nil
}

// GetAvailableGPUTypes returns all available GPU types the cloud provider supports.
// Contabo nodes have no GPU types.
func (s *Server) GetAvailableGPUTypes(_ context.Context, _ *protos.GetAvailableGPUTypesRequest) (*protos.GetAvailableGPUTypesResponse, error) {
	return &protos.GetAvailableGPUTypesResponse{}, nil
}

// PricingNodePrice returns a theoretical minimum price of running a node.
// Not implemented for Contabo; returns an empty response (pricing optional per spec).
func (s *Server) PricingNodePrice(_ context.Context, _ *protos.PricingNodePriceRequest) (*protos.PricingNodePriceResponse, error) {
	return &protos.PricingNodePriceResponse{}, nil
}

// PricingPodPrice returns a theoretical minimum price of running a pod.
// Not implemented for Contabo; returns an empty response (pricing optional per spec).
func (s *Server) PricingPodPrice(_ context.Context, _ *protos.PricingPodPriceRequest) (*protos.PricingPodPriceResponse, error) {
	return &protos.PricingPodPriceResponse{}, nil
}

// NodeGroupGetOptions returns autoscaling options for the node group.
// Returns an empty options set; real per-group options are added in later tasks.
func (s *Server) NodeGroupGetOptions(_ context.Context, _ *protos.NodeGroupAutoscalingOptionsRequest) (*protos.NodeGroupAutoscalingOptionsResponse, error) {
	return &protos.NodeGroupAutoscalingOptionsResponse{}, nil
}
