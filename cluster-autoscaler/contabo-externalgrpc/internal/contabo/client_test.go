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
}
