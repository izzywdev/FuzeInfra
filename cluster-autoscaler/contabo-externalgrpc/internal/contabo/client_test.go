package contabo

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestListByTag(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/v1/compute/instances") {
			w.Write([]byte(`{"data":[{"instanceId":42,"displayName":"fuzeinfra-elastic-0","status":"running","addresses":{"private":[{"ip":"10.0.0.5"}]},"tags":[{"name":"fuzeinfra-elastic"}]}]}`))
			return
		}
		w.Write([]byte(`{"access_token":"tok","expires_in":300}`)) // token endpoint
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL})
	got, err := c.ListByTag(context.Background(), "fuzeinfra-elastic")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].ID != 42 || got[0].Name != "fuzeinfra-elastic-0" {
		t.Fatalf("unexpected: %+v", got)
	}
	if got[0].PrivateIP != "10.0.0.5" {
		t.Fatalf("expected PrivateIP=10.0.0.5, got %s", got[0].PrivateIP)
	}
	if got[0].Status != "running" {
		t.Fatalf("expected Status=running, got %s", got[0].Status)
	}
}

func TestCreateAndDelete(t *testing.T) {
	var created, deleted bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "POST" && strings.Contains(r.URL.Path, "/instances"):
			created = true
			w.Write([]byte(`{"data":[{"instanceId":99,"displayName":"fuzeinfra-elastic-1","status":"provisioning"}]}`))
		case r.Method == "DELETE" && strings.Contains(r.URL.Path, "/instances/99"):
			deleted = true
			w.WriteHeader(204)
		default:
			w.Write([]byte(`{"access_token":"tok","expires_in":300}`))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL})
	inst, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-1", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config", Tags: []string{"fuzeinfra-elastic"}})
	if err != nil || inst.ID != 99 {
		t.Fatalf("create: %v %+v", err, inst)
	}
	if err := c.Delete(context.Background(), 99); err != nil {
		t.Fatal(err)
	}
	if !created || !deleted {
		t.Fatal("endpoints not hit")
	}
}
