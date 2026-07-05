package contabo

import (
	"context"
	"testing"
)

func TestMemClient_CreateListDeleteRoundTrip(t *testing.T) {
	ctx := context.Background()
	c := NewMemClient()

	created, err := c.Create(ctx, CreateReq{
		Name: "fuzeinfra-elastic-0",
		Tags: []string{"fuzeinfra-elastic"},
	})
	if err != nil {
		t.Fatalf("Create returned unexpected error: %v", err)
	}
	if created.ID == 0 {
		t.Fatalf("Create: got ID 0, want a non-zero assigned ID")
	}
	if created.Name != "fuzeinfra-elastic-0" {
		t.Errorf("Create: Name = %q, want fuzeinfra-elastic-0", created.Name)
	}
	if created.Status != "running" {
		t.Errorf("Create: Status = %q, want running", created.Status)
	}

	listed, err := c.ListByTag(ctx, "fuzeinfra-elastic")
	if err != nil {
		t.Fatalf("ListByTag returned unexpected error: %v", err)
	}
	if len(listed) != 1 {
		t.Fatalf("ListByTag: got %d instances, want 1", len(listed))
	}
	if listed[0].ID != created.ID {
		t.Errorf("ListByTag: ID = %d, want %d", listed[0].ID, created.ID)
	}

	if err := c.Delete(ctx, created.ID); err != nil {
		t.Fatalf("Delete returned unexpected error: %v", err)
	}

	listedAfterDelete, err := c.ListByTag(ctx, "fuzeinfra-elastic")
	if err != nil {
		t.Fatalf("ListByTag after delete returned unexpected error: %v", err)
	}
	if len(listedAfterDelete) != 0 {
		t.Fatalf("ListByTag after delete: got %d instances, want 0", len(listedAfterDelete))
	}
}

func TestMemClient_ListByTag_FiltersByTag(t *testing.T) {
	ctx := context.Background()
	c := NewMemClient()

	elasticA, err := c.Create(ctx, CreateReq{Name: "elastic-a", Tags: []string{"fuzeinfra-elastic"}})
	if err != nil {
		t.Fatalf("Create elastic-a returned unexpected error: %v", err)
	}
	elasticB, err := c.Create(ctx, CreateReq{Name: "elastic-b", Tags: []string{"fuzeinfra-elastic"}})
	if err != nil {
		t.Fatalf("Create elastic-b returned unexpected error: %v", err)
	}
	_, err = c.Create(ctx, CreateReq{Name: "other", Tags: []string{"some-other-tag"}})
	if err != nil {
		t.Fatalf("Create other returned unexpected error: %v", err)
	}
	_, err = c.Create(ctx, CreateReq{Name: "untagged"})
	if err != nil {
		t.Fatalf("Create untagged returned unexpected error: %v", err)
	}

	elastic, err := c.ListByTag(ctx, "fuzeinfra-elastic")
	if err != nil {
		t.Fatalf("ListByTag(fuzeinfra-elastic) returned unexpected error: %v", err)
	}
	if len(elastic) != 2 {
		t.Fatalf("ListByTag(fuzeinfra-elastic): got %d instances, want 2 (filtering must exclude non-matching tags)", len(elastic))
	}
	gotIDs := map[int64]bool{elastic[0].ID: true, elastic[1].ID: true}
	if !gotIDs[elasticA.ID] || !gotIDs[elasticB.ID] {
		t.Errorf("ListByTag(fuzeinfra-elastic): got IDs %v, want %d and %d", gotIDs, elasticA.ID, elasticB.ID)
	}

	other, err := c.ListByTag(ctx, "some-other-tag")
	if err != nil {
		t.Fatalf("ListByTag(some-other-tag) returned unexpected error: %v", err)
	}
	if len(other) != 1 {
		t.Fatalf("ListByTag(some-other-tag): got %d instances, want 1", len(other))
	}
	if other[0].Name != "other" {
		t.Errorf("ListByTag(some-other-tag): Name = %q, want other", other[0].Name)
	}

	none, err := c.ListByTag(ctx, "no-such-tag")
	if err != nil {
		t.Fatalf("ListByTag(no-such-tag) returned unexpected error: %v", err)
	}
	if len(none) != 0 {
		t.Fatalf("ListByTag(no-such-tag): got %d instances, want 0", len(none))
	}
}

func TestMemClient_Delete_NotFoundReturnsError(t *testing.T) {
	ctx := context.Background()
	c := NewMemClient()

	if err := c.Delete(ctx, 999); err == nil {
		t.Fatalf("Delete of nonexistent id: want error, got nil")
	}
}

func TestMemClient_Create_IncrementingIDs(t *testing.T) {
	ctx := context.Background()
	c := NewMemClient()

	first, err := c.Create(ctx, CreateReq{Name: "first"})
	if err != nil {
		t.Fatalf("Create first returned unexpected error: %v", err)
	}
	second, err := c.Create(ctx, CreateReq{Name: "second"})
	if err != nil {
		t.Fatalf("Create second returned unexpected error: %v", err)
	}
	if second.ID <= first.ID {
		t.Errorf("Create: second.ID = %d, want > first.ID = %d", second.ID, first.ID)
	}
}
