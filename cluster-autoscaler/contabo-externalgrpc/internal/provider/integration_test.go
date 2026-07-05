package provider_test

import (
	"context"
	"net"
	"testing"

	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/protos"
	"github.com/izzywdev/fuzeinfra/contabo-externalgrpc/internal/provider"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"
)

const bufSize = 1024 * 1024

// TestGRPCRoundTrip starts a real grpc.Server backed by provider.New (using the
// fakeCloud test double from server_test.go) over an in-process bufconn listener,
// dials it with a real gRPC client, and exercises NodeGroups + NodeGroupIncreaseSize
// end-to-end through the wire protocol (not just direct Go calls).
func TestGRPCRoundTrip(t *testing.T) {
	lis := bufconn.Listen(bufSize)

	fc := &fakeCloud{}
	cfg := provider.Config{
		ElasticTag: "fuzeinfra-elastic",
		NamePrefix: "fuzeinfra-elastic",
		ProductID:  "prod-123",
		ImageID:    "img-456",
		Region:     "us-central",
		SSHKeyID:   789,
		MinSize:    0,
		MaxSize:    2,
	}
	srv := provider.New(cfg, fc)

	grpcServer := grpc.NewServer()
	protos.RegisterCloudProviderServer(grpcServer, srv)

	go func() {
		if err := grpcServer.Serve(lis); err != nil {
			t.Logf("grpcServer.Serve exited: %v", err)
		}
	}()
	defer grpcServer.Stop()

	dialer := func(context.Context, string) (net.Conn, error) {
		return lis.Dial()
	}

	conn, err := grpc.NewClient(
		"passthrough:///bufnet",
		grpc.WithContextDialer(dialer),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		t.Fatalf("grpc.NewClient: %v", err)
	}
	defer conn.Close()

	client := protos.NewCloudProviderClient(conn)

	// NodeGroups should return exactly one group with Id=="elastic".
	ngResp, err := client.NodeGroups(context.Background(), &protos.NodeGroupsRequest{})
	if err != nil {
		t.Fatalf("NodeGroups: %v", err)
	}
	if len(ngResp.NodeGroups) != 1 {
		t.Fatalf("want 1 node group, got %d", len(ngResp.NodeGroups))
	}
	if ngResp.NodeGroups[0].Id != "elastic" {
		t.Fatalf("want node group Id=elastic, got %q", ngResp.NodeGroups[0].Id)
	}

	// NodeGroupIncreaseSize{Id:"elastic",Delta:1} should succeed and record 1 create.
	_, err = client.NodeGroupIncreaseSize(context.Background(), &protos.NodeGroupIncreaseSizeRequest{
		Id:    "elastic",
		Delta: 1,
	})
	if err != nil {
		t.Fatalf("NodeGroupIncreaseSize: %v", err)
	}
	if len(fc.instances) != 1 {
		t.Fatalf("want exactly 1 created instance, got %d", len(fc.instances))
	}
}
