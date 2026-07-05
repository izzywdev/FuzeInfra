package provider

import "github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"

// Compile-time assertion: *Server must satisfy the full CloudProviderServer interface.
// If any concrete method is missing or has the wrong signature, this line causes a
// build failure, surfacing the mismatch before runtime.
var _ protos.CloudProviderServer = (*Server)(nil)
