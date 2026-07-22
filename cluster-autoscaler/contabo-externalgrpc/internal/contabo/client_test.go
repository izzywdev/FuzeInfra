package contabo

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// tokenResponse is what the mock AuthURL returns for every request that
// doesn't match a more specific route below (mirrors the real Keycloak
// password-grant response shape).
const tokenResponse = `{"access_token":"tok","expires_in":300}`

func TestListByTag(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			if r.URL.Query().Get("name") != "fuzeinfra-elastic" {
				t.Errorf("expected name filter fuzeinfra-elastic, got %q", r.URL.Query().Get("name"))
			}
			w.Write([]byte(`{"data":[{"tagId":7,"name":"fuzeinfra-elastic","color":"#0A78C3"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags/7/assignments":
			w.Write([]byte(`{"data":[{"tagId":7,"tagName":"fuzeinfra-elastic","resourceType":"instance","resourceId":"42","resourceName":"fuzeinfra-elastic-0"}],"_pagination":{"totalElements":1}}`))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/v1/compute/instances"):
			w.Write([]byte(`{"data":[{"instanceId":42,"displayName":"fuzeinfra-elastic-0","status":"running","addresses":{"private":[{"ip":"10.0.0.5"}]}},{"instanceId":43,"displayName":"other-untagged","status":"running","addresses":{"private":[{"ip":"10.0.0.6"}]}}]}`))
		default:
			w.Write([]byte(tokenResponse)) // token endpoint
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	got, err := c.ListByTag(context.Background(), "fuzeinfra-elastic")
	if err != nil {
		t.Fatal(err)
	}
	// Instance 43 is NOT in the tag-assignments response, so it must be
	// excluded even though it appears in GET /v1/compute/instances.
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

// TestListByTag_ParsesCreatedDate verifies that the createdDate field on the
// GET /v1/compute/instances response is parsed into Instance.CreatedDate as
// an RFC3339 time.Time — the billing-aware reaper (internal/reaper) anchors
// its monthly-renewal calculation on this field, so a silent parse failure
// would make it guess wrong about when an instance renews.
func TestListByTag_ParsesCreatedDate(t *testing.T) {
	const created = "2026-01-15T10:30:00Z"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			w.Write([]byte(`{"data":[{"tagId":7,"name":"fuzeinfra-elastic","color":"#0A78C3"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags/7/assignments":
			w.Write([]byte(`{"data":[{"tagId":7,"tagName":"fuzeinfra-elastic","resourceType":"instance","resourceId":"42","resourceName":"fuzeinfra-elastic-0"}],"_pagination":{"totalElements":1}}`))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/v1/compute/instances"):
			w.Write([]byte(`{"data":[{"instanceId":42,"displayName":"fuzeinfra-elastic-0","status":"running","createdDate":"` + created + `","addresses":{"private":[{"ip":"10.0.0.5"}]}}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	got, err := c.ListByTag(context.Background(), "fuzeinfra-elastic")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 instance, got %d", len(got))
	}
	want, err := time.Parse(time.RFC3339, created)
	if err != nil {
		t.Fatalf("test setup: parsing expected time: %v", err)
	}
	if !got[0].CreatedDate.Equal(want) {
		t.Fatalf("CreatedDate = %v, want %v", got[0].CreatedDate, want)
	}
}

// TestListByTag_MissingCreatedDateStaysZero verifies that when the API
// response omits createdDate entirely (or it fails to parse), CreatedDate is
// left as the zero time.Time rather than erroring the whole call — that
// field is metadata for the reaper, not required for CA's core scale-up/down
// path, and other tests in this file rely on responses that never set it.
func TestListByTag_MissingCreatedDateStaysZero(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			w.Write([]byte(`{"data":[{"tagId":7,"name":"fuzeinfra-elastic","color":"#0A78C3"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags/7/assignments":
			w.Write([]byte(`{"data":[{"tagId":7,"tagName":"fuzeinfra-elastic","resourceType":"instance","resourceId":"42","resourceName":"fuzeinfra-elastic-0"}],"_pagination":{"totalElements":1}}`))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/v1/compute/instances"):
			w.Write([]byte(`{"data":[{"instanceId":42,"displayName":"fuzeinfra-elastic-0","status":"running"}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	got, err := c.ListByTag(context.Background(), "fuzeinfra-elastic")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 {
		t.Fatalf("expected 1 instance, got %d", len(got))
	}
	if !got[0].CreatedDate.IsZero() {
		t.Fatalf("expected zero CreatedDate when createdDate is absent, got %v", got[0].CreatedDate)
	}
}

// TestListByTag_NoSuchTag verifies that when the tag itself has never been
// created (GET /v1/tags finds no exact-name match), ListByTag returns an
// empty result instead of erroring — and never calls the assignments or
// compute/instances endpoints, since there's nothing to look up.
func TestListByTag_NoSuchTag(t *testing.T) {
	var calledAssignments, calledInstances bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			w.Write([]byte(`{"data":[]}`))
		case r.Method == http.MethodGet && strings.Contains(r.URL.Path, "/assignments"):
			calledAssignments = true
			w.Write([]byte(`{"data":[],"_pagination":{"totalElements":0}}`))
		case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/v1/compute/instances"):
			calledInstances = true
			w.Write([]byte(`{"data":[]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	got, err := c.ListByTag(context.Background(), "no-such-tag")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("expected no instances, got %+v", got)
	}
	if calledAssignments || calledInstances {
		t.Fatalf("expected no assignments/instances lookups for a nonexistent tag, calledAssignments=%v calledInstances=%v", calledAssignments, calledInstances)
	}
}

// TestCreateAndDelete exercises the full happy path: create an instance,
// resolve+create its tag, assign the tag, then cancel (Delete) it.
func TestCreateAndDelete(t *testing.T) {
	var created, tagCreated, assigned, canceled bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/compute/instances":
			created = true
			w.Write([]byte(`{"data":[{"instanceId":99,"displayName":"fuzeinfra-elastic-1","status":"provisioning"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			// Tag doesn't exist yet.
			w.Write([]byte(`{"data":[]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tags":
			tagCreated = true
			var body map[string]interface{}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body["name"] != "fuzeinfra-elastic" {
				t.Errorf("expected create-tag name=fuzeinfra-elastic, got %v", body["name"])
			}
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"data":[{"tenantId":"t","customerId":"c","tagId":7}]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tags/7/assignments/instance/99":
			assigned = true
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"_links":{}}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/compute/instances/99/cancel":
			canceled = true
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"data":[{"instanceId":99,"cancelDate":"2026-08-01"}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	inst, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-1", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config", Tags: []string{"fuzeinfra-elastic"}})
	if err != nil || inst.ID != 99 {
		t.Fatalf("create: %v %+v", err, inst)
	}
	if err := c.Delete(context.Background(), 99); err != nil {
		t.Fatal(err)
	}
	if !created || !tagCreated || !assigned || !canceled {
		t.Fatalf("endpoints not all hit: created=%v tagCreated=%v assigned=%v canceled=%v", created, tagCreated, assigned, canceled)
	}
}

// TestCreate_ReusesExistingTag verifies that when the tag already exists
// (GET /v1/tags finds an exact-name match), Create does NOT call POST
// /v1/tags again — it goes straight to assigning the existing tag id.
func TestCreate_ReusesExistingTag(t *testing.T) {
	var tagCreatePosted, assigned bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/compute/instances":
			w.Write([]byte(`{"data":[{"instanceId":55,"displayName":"fuzeinfra-elastic-2","status":"provisioning"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			w.Write([]byte(`{"data":[{"tagId":9,"name":"fuzeinfra-elastic","color":"#0A78C3"}]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tags":
			tagCreatePosted = true
			w.Write([]byte(`{"data":[{"tenantId":"t","customerId":"c","tagId":9}]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tags/9/assignments/instance/55":
			assigned = true
			w.WriteHeader(http.StatusCreated)
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	inst, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-2", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config", Tags: []string{"fuzeinfra-elastic"}})
	if err != nil || inst.ID != 55 {
		t.Fatalf("create: %v %+v", err, inst)
	}
	if tagCreatePosted {
		t.Fatal("expected POST /v1/tags to be skipped when the tag already exists")
	}
	if !assigned {
		t.Fatal("expected the existing tag id to be assigned to the new instance")
	}
}

// TestCreate_RollsBackOnTagAssignFailure verifies the leak-prevention
// behavior: if the instance is created but the tag can never be assigned
// (e.g. the assignment endpoint keeps failing), Create rolls the instance
// back (cancels it) and returns an error, rather than handing back a
// "successful" but untagged/untrackable instance — an untagged instance
// would never be found by ListByTag again, so cluster-autoscaler could
// never account for or retry it, and a later create with the same
// displayName would then hit Contabo's "duplicate display name" 400.
func TestCreate_RollsBackOnTagAssignFailure(t *testing.T) {
	var canceled bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/v1/compute/instances":
			w.Write([]byte(`{"data":[{"instanceId":123,"displayName":"fuzeinfra-elastic-3","status":"provisioning"}]}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v1/tags":
			w.Write([]byte(`{"data":[{"tagId":3,"name":"fuzeinfra-elastic","color":"#0A78C3"}]}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/tags/3/assignments/instance/123":
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte(`{"message":"internal error"}`))
		case r.Method == http.MethodPost && r.URL.Path == "/v1/compute/instances/123/cancel":
			canceled = true
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"data":[{"instanceId":123,"cancelDate":"2026-08-01"}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	_, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-3", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config", Tags: []string{"fuzeinfra-elastic"}})
	if err == nil {
		t.Fatal("expected Create to return an error when tag assignment fails")
	}
	if !canceled {
		t.Fatal("expected Create to roll back (cancel) the instance when tag assignment fails")
	}
}

// TestDelete_UsesCancelEndpoint verifies Delete calls POST
// /v1/compute/instances/{id}/cancel — NOT DELETE /v1/compute/instances/{id}
// (which a live spike test confirmed returns HTTP 404 against the real
// Contabo API).
func TestDelete_UsesCancelEndpoint(t *testing.T) {
	var gotMethod, gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/v1/compute/instances/") {
			gotMethod = r.Method
			gotPath = r.URL.Path
			w.WriteHeader(http.StatusCreated)
			w.Write([]byte(`{"data":[{"instanceId":99,"cancelDate":"2026-08-01"}]}`))
			return
		}
		w.Write([]byte(tokenResponse))
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	if err := c.Delete(context.Background(), 99); err != nil {
		t.Fatal(err)
	}
	if gotMethod != http.MethodPost || gotPath != "/v1/compute/instances/99/cancel" {
		t.Fatalf("expected POST /v1/compute/instances/99/cancel, got %s %s", gotMethod, gotPath)
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
			w.Write([]byte(`{"data":[{"instanceId":100,"displayName":"fuzeinfra-elastic-4","status":"provisioning"}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	if _, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-4", ProductID: "V45", ImageID: "img", Region: "EU", UserData: "#cloud-config"}); err != nil {
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
			w.Write([]byte(`{"data":[{"instanceId":101,"displayName":"fuzeinfra-elastic-5","status":"provisioning"}]}`))
		default:
			w.Write([]byte(tokenResponse))
		}
	}))
	defer srv.Close()
	c := NewClient(Config{BaseURL: srv.URL, AuthURL: srv.URL})
	if _, err := c.Create(context.Background(), CreateReq{Name: "fuzeinfra-elastic-5", ProductID: "V45", ImageID: "img", Region: "EU", SSHKeyID: 777, UserData: "#cloud-config"}); err != nil {
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
