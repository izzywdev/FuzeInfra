package contabo

import (
	"context"
	"fmt"
	"log"
	"sync"
)

// MemClient is an in-memory implementation of Client, used to exercise the
// full CA-to-provider loop (IncreaseSize/DeleteNodes) in integration tests
// and local/kind demos without ever touching the real Contabo API. See
// cmd/server's FAKE_CLOUD env switch, which constructs a MemClient instead of
// an HTTPClient.
//
// Every mutating call is logged with a distinctive "[fake-cloud]" prefix so
// integration tests can assert on cluster-autoscaler's behavior by grepping
// the provider pod's logs (e.g. `kubectl logs ... | grep '\[fake-cloud\] Create'`).
type MemClient struct {
	mu        sync.Mutex
	instances []Instance
	nextID    int64
}

// NewMemClient returns a MemClient with no instances and an ID counter
// starting at 1.
func NewMemClient() *MemClient {
	return &MemClient{
		instances: []Instance{},
		nextID:    1,
	}
}

// ListByTag returns all in-memory instances carrying the given tag.
func (m *MemClient) ListByTag(_ context.Context, tag string) ([]Instance, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	var matches []Instance
	for _, inst := range m.instances {
		for _, t := range inst.Tags {
			if t == tag {
				matches = append(matches, inst)
				break
			}
		}
	}

	log.Printf("[fake-cloud] ListByTag tag=%q matched=%d", tag, len(matches))
	return matches, nil
}

// Create appends a new running instance to the in-memory slice, assigning it
// the next incrementing ID.
func (m *MemClient) Create(_ context.Context, req CreateReq) (Instance, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	inst := Instance{
		ID:     m.nextID,
		Name:   req.Name,
		Status: "running",
		Tags:   append([]string(nil), req.Tags...),
	}
	m.nextID++
	m.instances = append(m.instances, inst)

	log.Printf("[fake-cloud] Create id=%d name=%q tags=%v productId=%q imageId=%q region=%q",
		inst.ID, inst.Name, inst.Tags, req.ProductID, req.ImageID, req.Region)

	return inst, nil
}

// Delete removes the instance with the given id from the in-memory slice.
// Returns an error if no instance with that id exists, matching the
// not-found convention the caller (provider.Server) expects to wrap into a
// gRPC status error.
func (m *MemClient) Delete(_ context.Context, id int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	for i, inst := range m.instances {
		if inst.ID == id {
			m.instances = append(m.instances[:i], m.instances[i+1:]...)
			log.Printf("[fake-cloud] Delete id=%d", id)
			return nil
		}
	}

	log.Printf("[fake-cloud] Delete id=%d not found", id)
	return fmt.Errorf("instance %d not found", id)
}
