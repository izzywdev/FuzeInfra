package contabo

import (
	"context"
	"encoding/json"
	"io"
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
		case r.Method == "POST" && r.URL.Path == "/v1/compute/instances":
			created = true
			w.Write([]byte(`{"data":[{"instanceId":99,"displayName":"fuzeinfra-elastic-1","status":"provisioning"}]}`))
		case r.Method == "POST" && strings.Contains(r.URL.Path, "/tag-assignments"):
			// Tag assignment endpoint
			w.WriteHeader(200)
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

// TestCreate_SSHKeysOmittedWhenZero verifies that when CreateReq.SSHKeyID is
// unset (0), the create-instance request body has NO "sshKeys" field at all
// — not an empty array, not [0]. The FuzeInfra Contabo account has zero
// registered SSH-key secrets, so referencing any secret id (including 0)
// would make every real create call fail; SSH access instead comes from
// cloud-init (see deploy/elastic-userdata.template).
func TestCreate_SSHKeysOmittedWhenZero(t *testing.T) {
	var gotBody map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "POST" && r.URL.Path == "/v1/compute/instances":
			body, _ := io.ReadAll(r.Body)
			if err := json.Unmarshal(body, &gotBody); err != nil {
				t.Fatalf("unmarshal create request body: %v", err)
			}
			w.Write([]byte(`{"data":[{"instanceId":100,"displayName":"fuzeinfra-elastic-2","status":"provisioning"}]}`))
		default:
			w.Write([]byte(`{"access_token":"tok","expires_in":300}`))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL})
	if _, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-2", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config"}); err != nil {
		t.Fatalf("create: %v", err)
	}
	if _, ok := gotBody["sshKeys"]; ok {
		t.Fatalf("expected \"sshKeys\" to be omitted from the request body when SSHKeyID is unset, got: %v", gotBody["sshKeys"])
	}
}

// TestCreate_SSHKeysPresentWhenPositive verifies the inverse: when
// CreateReq.SSHKeyID is > 0, "sshKeys" IS present in the request body and
// contains exactly that id.
func TestCreate_SSHKeysPresentWhenPositive(t *testing.T) {
	var gotBody map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == "POST" && r.URL.Path == "/v1/compute/instances":
			body, _ := io.ReadAll(r.Body)
			if err := json.Unmarshal(body, &gotBody); err != nil {
				t.Fatalf("unmarshal create request body: %v", err)
			}
			w.Write([]byte(`{"data":[{"instanceId":101,"displayName":"fuzeinfra-elastic-3","status":"provisioning"}]}`))
		default:
			w.Write([]byte(`{"access_token":"tok","expires_in":300}`))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL})
	if _, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-3", ProductID: "V45", ImageID: "img", Region: "EU", SSHKeyID: 777, UserData: "#cloud-config"}); err != nil {
		t.Fatalf("create: %v", err)
	}
	sshKeys, ok := gotBody["sshKeys"]
	if !ok {
		t.Fatal("expected \"sshKeys\" to be present in the request body when SSHKeyID > 0")
	}
	arr, ok := sshKeys.([]interface{})
	if !ok || len(arr) != 1 || arr[0].(float64) != 777 {
		t.Fatalf("expected sshKeys=[777], got %v", sshKeys)
	}
}
