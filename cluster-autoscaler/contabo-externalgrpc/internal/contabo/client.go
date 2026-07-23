package contabo

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Config holds the Contabo API client configuration.
type Config struct {
	ClientID     string
	ClientSecret string
	User         string
	Pass         string
	AuthURL      string // optional; falls back to BaseURL
	BaseURL      string
}

// Instance represents a Contabo compute instance.
type Instance struct {
	ID        int64
	Name      string
	Status    string
	PrivateIP string
	Tags      []string
	// CreatedDate is the instance's creation timestamp, parsed from
	// Contabo's createdDate field on GET /v1/compute/instances (see
	// ListByTag). It anchors the monthly billing period the billing-aware
	// reaper (internal/reaper) uses to decide when an elastic instance is
	// about to renew. The zero value means the field was absent or
	// unparsable on the API response — callers MUST treat a zero
	// CreatedDate as "unknown" and skip any billing-period decision for
	// that instance rather than guessing.
	CreatedDate time.Time
	// CancelDate is set by Contabo once an instance's cancel (Delete) has
	// been requested; it is the REAL termination date (cancellation is
	// end-of-billing-period, never immediate — see Delete's doc comment
	// below). The zero value means the instance has NOT been cancelled.
	// Callers (the billing-aware reaper, internal/reaper) MUST treat a
	// non-zero CancelDate as the authoritative termination anchor and MUST
	// NOT re-cancel an instance that already carries one.
	CancelDate time.Time
}

// CreateReq is the request to create a new instance.
type CreateReq struct {
	Name      string
	ProductID string
	ImageID   string
	Region    string
	// SSHKeyID is the Contabo secrets-API SSH key id to register on the new
	// instance via the sshKeys field. Optional: when <= 0, "sshKeys" is
	// omitted from the create request entirely (no registered SSH-key
	// secret is referenced) — break-glass SSH access is instead provisioned
	// via cloud-init (UserData), which every node already receives.
	SSHKeyID int64
	// UserData is PLAIN cloud-config/cloud-init text (e.g. starting with
	// "#cloud-config"), NOT base64. Create sends it to Contabo's
	// POST /v1/compute/instances `userData` field verbatim — Contabo expects
	// plain text there, never base64 (see Create's doc comment on the
	// createBody literal for the incident this caused when it was wrongly
	// base64-encoded).
	UserData string
	Tags     []string
	// PrivateNetworking, when true, orders the paid Contabo "Private
	// Networking" add-on as part of the create request (addOns.privateNetworking).
	// A node born with the add-on can be attached to the private VLAN without a
	// separate /upgrade call (which is what returns HTTP 402 when the add-on is
	// missing). Default false: enable only during the coordinated private-VLAN
	// cutover — a node on private-only flannel cannot join a still-public cluster.
	PrivateNetworking bool
	// PrivateNetworkID, when > 0 (and PrivateNetworking is true), is the Contabo
	// private-network id the newly created instance is ASSIGNED to after it
	// becomes visible. Ordering the add-on grants the paid capability; this call
	// grants network MEMBERSHIP. Both are required for a truly zero-touch VLAN
	// node — without the assignment the node has the add-on but stays off the net.
	PrivateNetworkID int64
}

// privateNetworkingAddOn is the (currently empty) configuration object for the
// Contabo Private Networking add-on. Contabo's createInstance API expresses
// add-ons as a named object: {"addOns":{"privateNetworking":{}}}.
type privateNetworkingAddOn struct{}

// createAddOns is the "addOns" object of the createInstance request body.
type createAddOns struct {
	PrivateNetworking *privateNetworkingAddOn `json:"privateNetworking,omitempty"`
}

// Client defines the Contabo API client interface.
type Client interface {
	ListByTag(ctx context.Context, tag string) ([]Instance, error)
	// ListByNamePrefix lists ALL Contabo instances (regardless of tag state)
	// whose displayName starts with prefix, fully paginated. This is the
	// authoritative source for the cluster-autoscaler provider's hard
	// anti-runaway cap (see internal/provider/scale.go) — unlike ListByTag,
	// it also catches untagged orphans (e.g. an instance a tag-assignment
	// bug left untagged), which is exactly the failure mode that let the
	// provider's MaxSize cap be silently defeated in prod (see PR history).
	ListByNamePrefix(ctx context.Context, prefix string) ([]Instance, error)
	Create(ctx context.Context, req CreateReq) (Instance, error)
	Delete(ctx context.Context, id int64) error
}

// HTTPClient implements the Client interface.
type HTTPClient struct {
	cfg       Config
	hc        *http.Client
	mu        sync.Mutex
	tok       string
	tokExpiry time.Time

	// tagCache memoizes tag name -> Contabo tagId lookups (see resolveTagID)
	// so a create loop doesn't re-resolve/re-create the same tag on every
	// call.
	tagCacheMu sync.Mutex
	tagCache   map[string]int64

	// instancePollAttempts/instancePollInterval bound how long Create waits,
	// after a successful POST /v1/compute/instances, for the new instance to
	// become visible to GET /v1/compute/instances/{id} before attempting to
	// tag it. Contabo's API is eventually consistent: a tag-assignment call
	// issued immediately after create can 404 ("Entry Resource not found by
	// resourceId") because the instance hasn't propagated to the tag
	// subsystem yet (observed live in prod, e.g. instance 203459381). Total
	// wall-clock budget is (instancePollAttempts-1) * instancePollInterval,
	// i.e. ~36s at the defaults below.
	instancePollAttempts int
	instancePollInterval time.Duration

	// tagAssignRetryAttempts/tagAssignRetryInterval bound retries of the tag
	// assignment call itself when it still 404s (or 5xxs) even after
	// waitForInstanceVisible already confirmed the plain instance GET
	// succeeds — the tag subsystem can lag slightly behind the instance
	// subsystem, so this is a second, independent consistency window.
	tagAssignRetryAttempts int
	tagAssignRetryInterval time.Duration

	// cancelRetryAttempts/cancelRetryInterval bound retries of the cancel
	// (Delete) call on a 404/5xx. Delete is also used as the rollback path
	// when tagging ultimately fails, so a cancel that itself races Contabo's
	// eventual consistency must not be treated as a hard failure — otherwise
	// the rollback leaks the instance exactly like the bug it exists to fix.
	cancelRetryAttempts int
	cancelRetryInterval time.Duration
}

// tagColor is the color assigned to any tag this client creates. Contabo
// requires a 4-7 character hex value on POST /v1/tags; the value itself is
// cosmetic (shown in the Contabo control panel) and has no functional
// effect on tag-assignment/lookup.
const tagColor = "#0A78C3"

// errRetryable marks an error returned by a Contabo API call as transient
// (HTTP 404 from eventual consistency, or 5xx) — i.e. safe to retry with
// backoff rather than failing immediately. Wrapped via %w so callers use
// errors.Is(err, errRetryable) to decide whether to retry.
var errRetryable = errors.New("retryable contabo API error")

// isRetryableStatus reports whether an HTTP status from the Contabo API
// should be treated as transient/eventual-consistency (404 — the resource
// hasn't propagated yet — or any 5xx) rather than a hard failure.
func isRetryableStatus(code int) bool {
	return code == http.StatusNotFound || code >= 500
}

// NewClient creates a new Contabo API client.
func NewClient(cfg Config) *HTTPClient {
	return &HTTPClient{
		cfg:      cfg,
		hc:       &http.Client{Timeout: 30 * time.Second},
		tagCache: make(map[string]int64),

		instancePollAttempts: 10,
		instancePollInterval: 4 * time.Second,

		tagAssignRetryAttempts: 6,
		tagAssignRetryInterval: 5 * time.Second,

		cancelRetryAttempts: 5,
		cancelRetryInterval: 3 * time.Second,
	}
}

// sleepCtx blocks for d, or until ctx is cancelled (whichever comes first),
// returning ctx.Err() in the cancellation case so a retry loop stops
// promptly on shutdown instead of sleeping out its full backoff.
func sleepCtx(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// invalidateToken clears the cached token to force a refresh on the next call.
func (c *HTTPClient) invalidateToken() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.tok = ""
	c.tokExpiry = time.Time{}
}

// newRequestID generates a unique request ID for tracing.
func newRequestID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		// Fallback (crypto/rand effectively never fails). Still a valid UUID
		// shape — Contabo's API rejects a non-UUID x-request-id with HTTP 400.
		return fmt.Sprintf("00000000-0000-4000-8000-%012d", time.Now().UnixNano()%1_000_000_000_000)
	}
	// Contabo requires x-request-id to be a UUID; format 16 random bytes as
	// UUID v4 (raw hex is rejected with HTTP 400 on the compute API).
	b[6] = (b[6] & 0x0f) | 0x40 // version 4
	b[8] = (b[8] & 0x3f) | 0x80 // variant 10x
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// token returns a valid OAuth2 bearer token, refreshing if necessary.
func (c *HTTPClient) token(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Return cached token if not expired
	if c.tok != "" && time.Now().Before(c.tokExpiry) {
		return c.tok, nil
	}

	// Determine auth endpoint. Contabo's OAuth2 token endpoint is a Keycloak
	// realm endpoint that is used DIRECTLY (no "/oauth/token" suffix) and
	// expects a form-urlencoded password grant — NOT JSON. Using the wrong
	// URL/body shape returns HTTP 404.
	authURL := c.cfg.AuthURL
	if authURL == "" {
		authURL = "https://auth.contabo.com/auth/realms/contabo/protocol/openid-connect/token"
	}

	// Build the form-urlencoded password-grant body with real credentials.
	form := url.Values{}
	form.Set("client_id", c.cfg.ClientID)
	form.Set("client_secret", c.cfg.ClientSecret)
	form.Set("username", c.cfg.User)
	form.Set("password", c.cfg.Pass)
	form.Set("grant_type", "password")

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, authURL, strings.NewReader(form.Encode()))
	if err != nil {
		return "", fmt.Errorf("create token request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.hc.Do(req)
	if err != nil {
		return "", fmt.Errorf("token request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("token request failed with status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode token response: %w", err)
	}

	c.tok = result.AccessToken
	// Cache until 10 seconds before expiry (or use full expiry if <= 10 seconds)
	ttl := result.ExpiresIn
	if ttl > 10 {
		ttl -= 10
	}
	c.tokExpiry = time.Now().Add(time.Duration(ttl) * time.Second)

	return c.tok, nil
}

// ListByTag lists all instances carrying the given tag name.
//
// Contabo's compute-instance resource (GET /v1/compute/instances) does not
// carry its own assigned tags — tags are a separate first-class resource
// with their own Tag Assignments API
// (https://api.contabo.com/#tag/Tag-Assignments). Membership is therefore
// resolved via that API instead of trusting an `.tags[]` field on the
// instance object:
//  1. resolve the tag id for `tag` (GET /v1/tags?name=...)
//  2. GET /v1/tags/{tagId}/assignments to collect the assigned instance ids
//  3. GET /v1/compute/instances (known-good, used elsewhere) for
//     Name/Status/PrivateIP, filtered down to the assigned id set
func (c *HTTPClient) ListByTag(ctx context.Context, tag string) ([]Instance, error) {
	tok, err := c.token(ctx)
	if err != nil {
		return nil, fmt.Errorf("get token: %w", err)
	}

	tagID, err := c.lookupTagID(ctx, tok, tag)
	if err != nil {
		return nil, fmt.Errorf("resolve tag %q: %w", tag, err)
	}
	if tagID == 0 {
		// The tag has never been created, so nothing can be assigned to it.
		return nil, nil
	}

	assignedIDs, err := c.listAssignedInstanceIDs(ctx, tok, tagID)
	if err != nil {
		return nil, fmt.Errorf("list assignments for tag %q (id=%d): %w", tag, tagID, err)
	}
	if len(assignedIDs) == 0 {
		return nil, nil
	}

	// GET /v1/compute/instances
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.cfg.BaseURL+"/v1/compute/instances", nil)
	if err != nil {
		return nil, fmt.Errorf("create instances request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: GET /v1/compute/instances failed: %v", err)
		return nil, fmt.Errorf("instances request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: GET /v1/compute/instances -> %d", resp.StatusCode)
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("instances request failed with status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Data []struct {
			InstanceID  int64  `json:"instanceId"`
			DisplayName string `json:"displayName"`
			Status      string `json:"status"`
			CreatedDate string `json:"createdDate"`
			CancelDate  string `json:"cancelDate"`
			Addresses   struct {
				Private []struct {
					IP string `json:"ip"`
				} `json:"private"`
			} `json:"addresses"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode instances response: %w", err)
	}

	// Filter instances down to the ones the Tag Assignments API reported as
	// carrying `tag`.
	var instances []Instance
	for _, inst := range result.Data {
		if !assignedIDs[inst.InstanceID] {
			continue
		}

		// Extract private IP
		privateIP := ""
		if len(inst.Addresses.Private) > 0 {
			privateIP = inst.Addresses.Private[0].IP
		}

		createdDate, ok := parseContaboTime(inst.CreatedDate)
		if !ok && inst.CreatedDate != "" {
			// Non-empty but unparsable is a real anomaly worth flagging; an
			// empty field is expected from older mocks/fixtures.
			log.Printf("contabo: instance %d (%s) has unparsable createdDate %q; leaving CreatedDate zero (billing-period decisions will be skipped for it)", inst.InstanceID, inst.DisplayName, inst.CreatedDate)
		}

		cancelDate, ok := parseContaboTime(inst.CancelDate)
		if !ok && inst.CancelDate != "" {
			log.Printf("contabo: instance %d (%s) has unparsable cancelDate %q; leaving CancelDate zero", inst.InstanceID, inst.DisplayName, inst.CancelDate)
		}

		instances = append(instances, Instance{
			ID:          inst.InstanceID,
			Name:        inst.DisplayName,
			Status:      inst.Status,
			PrivateIP:   privateIP,
			Tags:        []string{tag},
			CreatedDate: createdDate,
			CancelDate:  cancelDate,
		})
	}

	return instances, nil
}

// listComputeInstancesPageSize/listComputeInstancesMaxPages bound
// listAllComputeInstances' page walk. 100 is Contabo's max page size; the
// 50-page cap (5000 instances) is far beyond any realistic FuzeInfra fleet
// and exists only so a pagination bug (e.g. a page that never comes back
// short) can't loop forever — hitting it is logged loudly since it means the
// result is a silent undercount, which matters a lot to a caller using it as
// a safety-cap authority (ListByNamePrefix).
const (
	listComputeInstancesPageSize = 100
	listComputeInstancesMaxPages = 50
)

// listAllComputeInstances fetches EVERY Contabo compute instance across all
// pages of GET /v1/compute/instances, decoding each into an Instance
// (Tags left nil — this is a global, not tag-scoped, listing; ListByTag sets
// Tags itself from the tag-assignments lookup it already did).
//
// Contabo's pagination is 1-based (page=1 is the first page, NOT page=0) —
// confirmed live via the ca-cleanup-orphans workflow
// (.github/workflows/ca-cleanup-orphans.yml) after page=0 silently returned
// the same first page forever.
func (c *HTTPClient) listAllComputeInstances(ctx context.Context, tok string) ([]Instance, error) {
	var all []Instance
	for page := 1; page <= listComputeInstancesMaxPages; page++ {
		items, err := c.fetchComputeInstancesPage(ctx, tok, page, listComputeInstancesPageSize)
		if err != nil {
			return nil, err
		}
		if len(items) == 0 {
			return all, nil
		}
		all = append(all, items...)
		if len(items) < listComputeInstancesPageSize {
			// A short page means this was the last one.
			return all, nil
		}
	}
	log.Printf("contabo: WARNING listAllComputeInstances hit the %d-page cap (%d instances collected) — the walk stopped early and the result may be an undercount", listComputeInstancesMaxPages, len(all))
	return all, nil
}

// fetchComputeInstancesPage fetches a single (1-based) page of GET
// /v1/compute/instances and decodes each entry into an Instance, including
// CreatedDate/CancelDate/PrivateIP (Tags is left nil; see
// listAllComputeInstances).
func (c *HTTPClient) fetchComputeInstancesPage(ctx context.Context, tok string, page, size int) ([]Instance, error) {
	u := fmt.Sprintf("%s/v1/compute/instances?page=%d&size=%d", c.cfg.BaseURL, page, size)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, fmt.Errorf("create instances request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: GET %s failed: %v", u, err)
		return nil, fmt.Errorf("instances request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: GET /v1/compute/instances?page=%d&size=%d -> %d", page, size, resp.StatusCode)
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("instances request (page=%d) failed with status %d: %s", page, resp.StatusCode, string(body))
	}

	var result struct {
		Data []struct {
			InstanceID  int64  `json:"instanceId"`
			DisplayName string `json:"displayName"`
			Status      string `json:"status"`
			CreatedDate string `json:"createdDate"`
			CancelDate  string `json:"cancelDate"`
			Addresses   struct {
				Private []struct {
					IP string `json:"ip"`
				} `json:"private"`
			} `json:"addresses"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode instances response (page=%d): %w", page, err)
	}

	items := make([]Instance, 0, len(result.Data))
	for _, inst := range result.Data {
		privateIP := ""
		if len(inst.Addresses.Private) > 0 {
			privateIP = inst.Addresses.Private[0].IP
		}

		createdDate, ok := parseContaboTime(inst.CreatedDate)
		if !ok && inst.CreatedDate != "" {
			log.Printf("contabo: instance %d (%s) has unparsable createdDate %q; leaving CreatedDate zero", inst.InstanceID, inst.DisplayName, inst.CreatedDate)
		}
		cancelDate, ok := parseContaboTime(inst.CancelDate)
		if !ok && inst.CancelDate != "" {
			log.Printf("contabo: instance %d (%s) has unparsable cancelDate %q; leaving CancelDate zero", inst.InstanceID, inst.DisplayName, inst.CancelDate)
		}

		items = append(items, Instance{
			ID:          inst.InstanceID,
			Name:        inst.DisplayName,
			Status:      inst.Status,
			PrivateIP:   privateIP,
			CreatedDate: createdDate,
			CancelDate:  cancelDate,
		})
	}
	return items, nil
}

// ListByNamePrefix implements Client.ListByNamePrefix. It walks every page
// of GET /v1/compute/instances (see listAllComputeInstances) and filters
// client-side on prefix, deliberately WITHOUT consulting tag state — see the
// interface doc comment on why: this is the authoritative count the
// provider's anti-runaway cap relies on, and must catch untagged orphans
// that ListByTag structurally cannot see.
func (c *HTTPClient) ListByNamePrefix(ctx context.Context, prefix string) ([]Instance, error) {
	tok, err := c.token(ctx)
	if err != nil {
		return nil, fmt.Errorf("get token: %w", err)
	}

	all, err := c.listAllComputeInstances(ctx, tok)
	if err != nil {
		return nil, fmt.Errorf("list all compute instances: %w", err)
	}

	var matched []Instance
	for _, inst := range all {
		if strings.HasPrefix(inst.Name, prefix) {
			matched = append(matched, inst)
		}
	}
	return matched, nil
}

// parseContaboTime parses a Contabo API timestamp field (e.g. createdDate),
// documented as an RFC3339 datetime. It tolerates both the fractional- and
// whole-second forms. Returns the zero time and false if s is empty or does
// not parse in either form — callers must treat that as "unknown", never as
// "epoch" (a zero time.Time would otherwise look like a decades-overdue
// billing anchor to any consumer that doesn't check ok).
func parseContaboTime(s string) (time.Time, bool) {
	if s == "" {
		return time.Time{}, false
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t, true
	}
	if t, err := time.Parse(time.RFC3339, s); err == nil {
		return t, true
	}
	return time.Time{}, false
}

// listAssignedInstanceIDs returns the set of instance ids that GET
// /v1/tags/{tagId}/assignments reports as carrying tagID, restricted to
// resourceType=instance assignments.
//
// NOTE: this issues a single page request (size=100). Contabo's assignments
// endpoint is paginated; if the elastic fleet ever exceeds ~100 tagged
// instances (far beyond anything FuzeInfra's autoscaler config allows today)
// this needs real page-walking. A mismatch between totalElements and the
// number of ids collected is logged loudly so that's visible before it
// silently drops nodes from tracking.
func (c *HTTPClient) listAssignedInstanceIDs(ctx context.Context, tok string, tagID int64) (map[int64]bool, error) {
	path := fmt.Sprintf("/v1/tags/%d/assignments?resourceType=instance&size=100", tagID)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.cfg.BaseURL+path, nil)
	if err != nil {
		return nil, fmt.Errorf("create list-assignments request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: GET %s failed: %v", path, err)
		return nil, fmt.Errorf("list-assignments request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: GET %s -> %d", path, resp.StatusCode)
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("list-assignments request failed with status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Data []struct {
			ResourceType string `json:"resourceType"`
			ResourceID   string `json:"resourceId"`
		} `json:"data"`
		Pagination struct {
			TotalElements int `json:"totalElements"`
		} `json:"_pagination"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode list-assignments response: %w", err)
	}

	if result.Pagination.TotalElements > len(result.Data) {
		log.Printf("contabo: WARNING GET %s returned %d/%d assignments (pagination not implemented) — some tagged instances may be missed", path, len(result.Data), result.Pagination.TotalElements)
	}

	ids := make(map[int64]bool, len(result.Data))
	for _, a := range result.Data {
		if a.ResourceType != "instance" {
			continue
		}
		id, err := strconv.ParseInt(a.ResourceID, 10, 64)
		if err != nil {
			log.Printf("contabo: skipping assignment with non-numeric resourceId %q: %v", a.ResourceID, err)
			continue
		}
		ids[id] = true
	}
	return ids, nil
}

// lookupTagID looks up an existing tag's id by exact name via GET /v1/tags.
// Returns (0, nil) — not an error — if no tag with that exact name exists;
// Contabo's `name` query parameter is a substring filter, so results are
// re-checked for an exact match client-side.
func (c *HTTPClient) lookupTagID(ctx context.Context, tok, name string) (int64, error) {
	u := c.cfg.BaseURL + "/v1/tags?name=" + url.QueryEscape(name) + "&size=100"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return 0, fmt.Errorf("create list-tags request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: GET /v1/tags failed: %v", err)
		return 0, fmt.Errorf("list-tags request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: GET /v1/tags?name=%s -> %d", name, resp.StatusCode)
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return 0, fmt.Errorf("list-tags request failed with status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Data []struct {
			TagID int64  `json:"tagId"`
			Name  string `json:"name"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0, fmt.Errorf("decode list-tags response: %w", err)
	}
	for _, t := range result.Data {
		if t.Name == name {
			return t.TagID, nil
		}
	}
	return 0, nil
}

// createTag creates a new Contabo tag named `name` via POST /v1/tags and
// returns its id. If a concurrent caller already created the same tag
// (HTTP 409 Conflict), it falls back to looking the tag up instead of
// failing.
func (c *HTTPClient) createTag(ctx context.Context, tok, name string) (int64, error) {
	reqBody, err := json.Marshal(struct {
		Name  string `json:"name"`
		Color string `json:"color"`
	}{Name: name, Color: tagColor})
	if err != nil {
		return 0, fmt.Errorf("marshal create-tag request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+"/v1/tags", bytes.NewReader(reqBody))
	if err != nil {
		return 0, fmt.Errorf("create create-tag request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: POST /v1/tags failed: %v", err)
		return 0, fmt.Errorf("create-tag request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: POST /v1/tags (name=%s) -> %d", name, resp.StatusCode)

	if resp.StatusCode == http.StatusConflict {
		// Another caller created this tag between our GET and this POST.
		return c.lookupTagID(ctx, tok, name)
	}
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		respBody, _ := io.ReadAll(resp.Body)
		return 0, fmt.Errorf("create-tag request failed with status %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		Data []struct {
			TagID int64 `json:"tagId"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return 0, fmt.Errorf("decode create-tag response: %w", err)
	}
	if len(result.Data) == 0 {
		return 0, errors.New("create-tag response contains no tag data")
	}
	return result.Data[0].TagID, nil
}

// resolveTagID returns the Contabo tag id for `name`, creating the tag if
// it doesn't already exist. Results are memoized on the client (tagCache)
// so a create-heavy scale-up loop doesn't re-resolve the same tag on every
// instance.
func (c *HTTPClient) resolveTagID(ctx context.Context, tok, name string) (int64, error) {
	c.tagCacheMu.Lock()
	if id, ok := c.tagCache[name]; ok {
		c.tagCacheMu.Unlock()
		return id, nil
	}
	c.tagCacheMu.Unlock()

	id, err := c.lookupTagID(ctx, tok, name)
	if err != nil {
		return 0, err
	}
	if id == 0 {
		id, err = c.createTag(ctx, tok, name)
		if err != nil {
			return 0, err
		}
	}

	c.tagCacheMu.Lock()
	c.tagCache[name] = id
	c.tagCacheMu.Unlock()
	return id, nil
}

// assignTag assigns tagID to instanceID via
// POST /v1/tags/{tagId}/assignments/instance/{instanceId} (no request
// body). See https://api.contabo.com/#tag/Tag-Assignments.
//
// A non-2xx response is wrapped with errRetryable when the status is 404 or
// 5xx (see isRetryableStatus) so assignTagWithRetry can distinguish a
// transient eventual-consistency failure from a hard one.
func (c *HTTPClient) assignTag(ctx context.Context, tok string, tagID, instanceID int64) error {
	path := fmt.Sprintf("/v1/tags/%d/assignments/instance/%d", tagID, instanceID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+path, nil)
	if err != nil {
		return fmt.Errorf("create tag-assignment request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: POST %s failed: %v", path, err)
		return fmt.Errorf("tag-assignment request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: POST %s -> %d", path, resp.StatusCode)

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		log.Printf("contabo: tag assignment (tagId=%d instanceId=%d) failed with status %d: %s", tagID, instanceID, resp.StatusCode, string(body))
		if isRetryableStatus(resp.StatusCode) {
			return fmt.Errorf("tag-assignment request failed with status %d: %s: %w", resp.StatusCode, string(body), errRetryable)
		}
		return fmt.Errorf("tag-assignment request failed with status %d: %s", resp.StatusCode, string(body))
	}
	return nil
}

// assignTagWithRetry calls assignTag, retrying up to tagAssignRetryAttempts
// times (fixed backoff of tagAssignRetryInterval between attempts) as long
// as the failure is retryable (see isRetryableStatus) — this is what closes
// the eventual-consistency race live prod hit: the tag subsystem can 404 a
// just-created instance even after waitForInstanceVisible's plain GET
// already succeeded. A non-retryable error is returned immediately.
func (c *HTTPClient) assignTagWithRetry(ctx context.Context, tok string, tagID, instanceID int64) error {
	var lastErr error
	for attempt := 0; attempt < c.tagAssignRetryAttempts; attempt++ {
		if attempt > 0 {
			if err := sleepCtx(ctx, c.tagAssignRetryInterval); err != nil {
				return err
			}
		}
		err := c.assignTag(ctx, tok, tagID, instanceID)
		if err == nil {
			return nil
		}
		lastErr = err
		if !errors.Is(err, errRetryable) {
			return err
		}
		log.Printf("contabo: tag assignment (tagId=%d instanceId=%d) retryable failure (attempt %d/%d): %v", tagID, instanceID, attempt+1, c.tagAssignRetryAttempts, err)
	}
	return fmt.Errorf("tag assignment (tagId=%d instanceId=%d) did not succeed after %d attempts: %w", tagID, instanceID, c.tagAssignRetryAttempts, lastErr)
}

// applyTags resolves (creating if necessary) and assigns every tag name in
// `tags` to instanceID, stopping at the first failure.
func (c *HTTPClient) applyTags(ctx context.Context, tok string, instanceID int64, tags []string) error {
	for _, name := range tags {
		tagID, err := c.resolveTagID(ctx, tok, name)
		if err != nil {
			return fmt.Errorf("resolve tag %q: %w", name, err)
		}
		if err := c.assignTagWithRetry(ctx, tok, tagID, instanceID); err != nil {
			return fmt.Errorf("assign tag %q (id=%d) to instance %d: %w", name, tagID, instanceID, err)
		}
	}
	return nil
}

// assignPrivateNetwork attaches instanceID to the Contabo private network
// networkID via POST /v1/private-networks/{networkId}/instances/{instanceId}
// (no request body). This is the step that puts an autoscaled node ON the VLAN
// — ordering the add-on (createBody addOns.privateNetworking) grants the paid
// capability, but network MEMBERSHIP is a separate call; without it, assign is
// never done and the node stays off the private net (the manual gap that would
// otherwise defeat zero-touch VLAN autoscaling). 404/5xx are wrapped retryable
// so assignPrivateNetworkWithRetry can ride out Contabo's eventual consistency.
func (c *HTTPClient) assignPrivateNetwork(ctx context.Context, tok string, networkID, instanceID int64) error {
	path := fmt.Sprintf("/v1/private-networks/%d/instances/%d", networkID, instanceID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+path, nil)
	if err != nil {
		return fmt.Errorf("create private-network-assignment request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: POST %s failed: %v", path, err)
		return fmt.Errorf("private-network-assignment request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: POST %s -> %d", path, resp.StatusCode)

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		log.Printf("contabo: private-network assignment (networkId=%d instanceId=%d) failed with status %d: %s", networkID, instanceID, resp.StatusCode, string(body))
		if isRetryableStatus(resp.StatusCode) {
			return fmt.Errorf("private-network-assignment request failed with status %d: %s: %w", resp.StatusCode, string(body), errRetryable)
		}
		return fmt.Errorf("private-network-assignment request failed with status %d: %s", resp.StatusCode, string(body))
	}
	return nil
}

// assignPrivateNetworkWithRetry calls assignPrivateNetwork, retrying retryable
// failures with the same fixed backoff used for tag assignment (Contabo's
// private-network subsystem has the same just-created eventual-consistency
// window). A non-retryable error is returned immediately.
func (c *HTTPClient) assignPrivateNetworkWithRetry(ctx context.Context, tok string, networkID, instanceID int64) error {
	var lastErr error
	for attempt := 0; attempt < c.tagAssignRetryAttempts; attempt++ {
		if attempt > 0 {
			if err := sleepCtx(ctx, c.tagAssignRetryInterval); err != nil {
				return err
			}
		}
		err := c.assignPrivateNetwork(ctx, tok, networkID, instanceID)
		if err == nil {
			return nil
		}
		lastErr = err
		if !errors.Is(err, errRetryable) {
			return err
		}
		log.Printf("contabo: private-network assignment (networkId=%d instanceId=%d) retryable failure (attempt %d/%d): %v", networkID, instanceID, attempt+1, c.tagAssignRetryAttempts, err)
	}
	return fmt.Errorf("private-network assignment (networkId=%d instanceId=%d) did not succeed after %d attempts: %w", networkID, instanceID, c.tagAssignRetryAttempts, lastErr)
}

// getInstance retrieves a single instance via GET
// /v1/compute/instances/{id}. It returns (true, nil) when Contabo reports
// the instance (HTTP 200), (false, nil) when Contabo returns 404 (not yet
// visible — the eventual-consistency case waitForInstanceVisible polls
// for), and a non-nil error for any other failure.
func (c *HTTPClient) getInstance(ctx context.Context, tok string, id int64) (bool, error) {
	path := fmt.Sprintf("/v1/compute/instances/%d", id)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.cfg.BaseURL+path, nil)
	if err != nil {
		return false, fmt.Errorf("create get-instance request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("x-request-id", newRequestID())

	resp, err := c.hc.Do(req)
	if err != nil {
		log.Printf("contabo: GET %s failed: %v", path, err)
		return false, fmt.Errorf("get-instance request failed: %w", err)
	}
	defer resp.Body.Close()

	log.Printf("contabo: GET %s -> %d", path, resp.StatusCode)

	if resp.StatusCode == http.StatusNotFound {
		return false, nil
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return false, fmt.Errorf("get-instance request failed with status %d: %s", resp.StatusCode, string(body))
	}
	return true, nil
}

// waitForInstanceVisible polls getInstance until Contabo reports the
// just-created instance as visible (HTTP 200), or the bounded retry budget
// (instancePollAttempts attempts, instancePollInterval apart) is exhausted.
//
// This closes the eventual-consistency race observed live in prod: Create
// succeeds (e.g. instance 203459381), but an immediate tag-assignment call
// 404s ("Entry Resource not found by resourceId") because the instance
// hasn't propagated to the tag subsystem yet. Waiting for the plain
// instance GET to succeed first, before ever calling the tag-assignment
// endpoint, closes most of that window; assignTagWithRetry closes the
// remainder (the tag subsystem can lag slightly behind the instance
// subsystem).
//
// A non-404 error from getInstance also just counts as a failed attempt
// (rather than returning immediately) since a transient 5xx during the same
// propagation window is plausible and no more actionable than a 404 here.
func (c *HTTPClient) waitForInstanceVisible(ctx context.Context, tok string, id int64) error {
	var lastErr error
	for attempt := 0; attempt < c.instancePollAttempts; attempt++ {
		if attempt > 0 {
			if err := sleepCtx(ctx, c.instancePollInterval); err != nil {
				return err
			}
		}
		ready, err := c.getInstance(ctx, tok, id)
		if err != nil {
			lastErr = err
			log.Printf("contabo: instance %d visibility check error (attempt %d/%d): %v", id, attempt+1, c.instancePollAttempts, err)
			continue
		}
		if ready {
			return nil
		}
		lastErr = fmt.Errorf("instance %d not yet visible (404)", id)
		log.Printf("contabo: instance %d not yet visible (attempt %d/%d)", id, attempt+1, c.instancePollAttempts)
	}
	return fmt.Errorf("instance %d did not become visible after %d attempts: %w", id, c.instancePollAttempts, lastErr)
}

// Create creates a new instance.
func (c *HTTPClient) Create(ctx context.Context, req CreateReq) (Instance, error) {
	// Helper to perform the actual create request
	doCreate := func(tok string) (Instance, bool, error) {
		// Prepare the request body. SSHKeys uses `omitempty` on a slice left
		// nil (not an empty-but-non-nil slice) whenever req.SSHKeyID <= 0, so
		// the "sshKeys" field is OMITTED from the marshaled JSON entirely in
		// that case rather than sent as [0]. This matters because the
		// FuzeInfra Contabo account has zero registered SSH-key secrets — SSH
		// access to elastic nodes is instead provisioned via cloud-init (see
		// deploy/elastic-userdata.template), and Contabo's create-instance API
		// accepts an omitted sshKeys field. Sending sshKeys:[0] (an
		// unregistered/invalid secret id) would make every create fail.
		var sshKeys []int64
		if req.SSHKeyID > 0 {
			sshKeys = []int64{req.SSHKeyID}
		}
		// Order the Private Networking add-on at create time only when asked.
		// nil (omitempty) => the addOns key is absent, i.e. no paid add-on.
		var addOns *createAddOns
		if req.PrivateNetworking {
			addOns = &createAddOns{PrivateNetworking: &privateNetworkingAddOn{}}
		}

		createBody := struct {
			DisplayName string        `json:"displayName"`
			ImageID     string        `json:"imageId"`
			ProductID   string        `json:"productId"`
			Region      string        `json:"region"`
			SSHKeys     []int64       `json:"sshKeys,omitempty"`
			UserData    string        `json:"userData"`
			AddOns      *createAddOns `json:"addOns,omitempty"`
		}{
			DisplayName: req.Name,
			ImageID:     req.ImageID,
			ProductID:   req.ProductID,
			Region:      req.Region,
			SSHKeys:     sshKeys,
			AddOns:      addOns,
			// PLAIN text, NOT base64. Contabo's POST /v1/compute/instances
			// userData field expects the raw cloud-config text as-is — this
			// was previously (wrongly) base64-encoded here, which made
			// Contabo deliver base64 gibberish as the instance's user-data;
			// cloud-init couldn't parse "#cloud-config" and silently skipped
			// it entirely, so NO created elastic instance ever got its SSH
			// key or joined k3s (confirmed live: zero join attempts logged
			// by the k3s server, break-glass SSH key rejected on a live
			// orphan). The working baseline nodes (Terraform
			// contabo_instance.user_data = templatefile(...)) always sent
			// plain text, which is why only the Go-provider-created elastic
			// nodes were ever affected. See CreateReq.UserData's doc comment
			// and deploy/elastic-userdata.template.
			UserData: req.UserData,
		}

		reqBodyBytes, err := json.Marshal(createBody)
		if err != nil {
			return Instance{}, false, fmt.Errorf("marshal create request: %w", err)
		}

		// POST /v1/compute/instances
		httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+"/v1/compute/instances", bytes.NewReader(reqBodyBytes))
		if err != nil {
			return Instance{}, false, fmt.Errorf("create create request: %w", err)
		}
		httpReq.Header.Set("Authorization", "Bearer "+tok)
		httpReq.Header.Set("Content-Type", "application/json")
		httpReq.Header.Set("x-request-id", newRequestID())

		resp, err := c.hc.Do(httpReq)
		if err != nil {
			return Instance{}, false, fmt.Errorf("create request failed: %w", err)
		}
		defer resp.Body.Close()

		// On 401, signal to retry with a fresh token
		if resp.StatusCode == http.StatusUnauthorized {
			return Instance{}, true, errors.New("unauthorized")
		}

		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
			body, _ := io.ReadAll(resp.Body)
			return Instance{}, false, fmt.Errorf("create request failed with status %d: %s", resp.StatusCode, string(body))
		}

		var result struct {
			Data []struct {
				InstanceID  int64  `json:"instanceId"`
				DisplayName string `json:"displayName"`
				Status      string `json:"status"`
			} `json:"data"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
			return Instance{}, false, fmt.Errorf("decode create response: %w", err)
		}

		if len(result.Data) == 0 {
			return Instance{}, false, errors.New("create response contains no instance data")
		}

		inst := result.Data[0]

		// Apply tags via the Tag Assignments API (see applyTags/assignTag).
		// FuzeInfra's whole tracking model is tag-based (ListByTag is how CA
		// finds existing elastic nodes), so an instance that got created but
		// NOT tagged is worse than an outright create failure: it becomes an
		// invisible zombie that (a) counts against the account/product quota
		// and (b) permanently blocks any future create using the same
		// displayName ("There is already an instance with the same display
		// name", HTTP 400) without ever showing up in ListByTag for CA to
		// retry against. So on tag failure we roll the instance back
		// (best-effort delete, itself retried — see Delete) rather than
		// returning it as a success — the instance must end up either
		// tagged, or gone.
		if len(req.Tags) > 0 {
			// Wait for the instance to become visible to a plain GET before
			// ever calling the tag-assignment endpoint (see
			// waitForInstanceVisible for why: Contabo's eventual consistency
			// means an immediate tag-assignment call can 404 even though
			// create just succeeded).
			if visErr := c.waitForInstanceVisible(ctx, tok, inst.InstanceID); visErr != nil {
				log.Printf("contabo: instance %d (%s) created but never became visible for tagging (%v); rolling back", inst.InstanceID, inst.DisplayName, visErr)
				if delErr := c.Delete(ctx, inst.InstanceID); delErr != nil {
					log.Printf("contabo: rollback cancel of instance %d (never became visible) ALSO failed: %v — instance %d may be LEAKED, needs manual cleanup", inst.InstanceID, delErr, inst.InstanceID)
					return Instance{}, false, fmt.Errorf("instance %d never became visible for tagging: %w (rollback cancel also failed: %v)", inst.InstanceID, visErr, delErr)
				}
				return Instance{}, false, fmt.Errorf("instance %d never became visible for tagging: %w (instance rolled back)", inst.InstanceID, visErr)
			}

			tagErr := c.applyTags(ctx, tok, inst.InstanceID, req.Tags)
			if tagErr != nil {
				log.Printf("contabo: instance %d (%s) created but tagging failed (%v); rolling back", inst.InstanceID, inst.DisplayName, tagErr)
				if delErr := c.Delete(ctx, inst.InstanceID); delErr != nil {
					log.Printf("contabo: rollback cancel of untagged instance %d ALSO failed: %v — instance %d may be LEAKED, needs manual cleanup", inst.InstanceID, delErr, inst.InstanceID)
					return Instance{}, false, fmt.Errorf("tag instance %d: %w (rollback cancel also failed: %v)", inst.InstanceID, tagErr, delErr)
				}
				return Instance{}, false, fmt.Errorf("tag instance %d: %w (instance rolled back)", inst.InstanceID, tagErr)
			}
		}

		// Attach to the private VLAN when requested. Ordering the add-on above
		// (createBody addOns) only grants the capability — this call grants
		// network MEMBERSHIP, and both are needed for a zero-touch VLAN node.
		// The instance is already confirmed visible (the tag block's
		// waitForInstanceVisible ran, since elastic nodes always carry a tag).
		// A VLAN-mode node that fails to attach is non-functional (its privnet
		// cloud-init binds flannel to eth1), so we roll it back — same policy as
		// a tag failure — rather than leave a broken node the cap must count.
		if req.PrivateNetworking && req.PrivateNetworkID > 0 {
			if pnErr := c.assignPrivateNetworkWithRetry(ctx, tok, req.PrivateNetworkID, inst.InstanceID); pnErr != nil {
				log.Printf("contabo: instance %d (%s) created+tagged but private-network attach failed (%v); rolling back", inst.InstanceID, inst.DisplayName, pnErr)
				if delErr := c.Delete(ctx, inst.InstanceID); delErr != nil {
					log.Printf("contabo: rollback cancel of unattached instance %d ALSO failed: %v — instance %d may be LEAKED, needs manual cleanup", inst.InstanceID, delErr, inst.InstanceID)
					return Instance{}, false, fmt.Errorf("attach instance %d to private network %d: %w (rollback cancel also failed: %v)", inst.InstanceID, req.PrivateNetworkID, pnErr, delErr)
				}
				return Instance{}, false, fmt.Errorf("attach instance %d to private network %d: %w (instance rolled back)", inst.InstanceID, req.PrivateNetworkID, pnErr)
			}
		}

		return Instance{
			ID:     inst.InstanceID,
			Name:   inst.DisplayName,
			Status: inst.Status,
			Tags:   req.Tags,
		}, false, nil
	}

	// First attempt
	tok, err := c.token(ctx)
	if err != nil {
		return Instance{}, fmt.Errorf("get token: %w", err)
	}

	inst, shouldRetry, err := doCreate(tok)
	if !shouldRetry {
		return inst, err
	}

	// Retry with fresh token
	c.invalidateToken()
	tok, err = c.token(ctx)
	if err != nil {
		return Instance{}, fmt.Errorf("get token (retry): %w", err)
	}

	inst, _, err = doCreate(tok)
	return inst, err
}

// Delete removes an instance via POST /v1/compute/instances/{id}/cancel.
//
// There is NO DELETE /v1/compute/instances/{id} endpoint — a live spike
// test against a real running instance confirmed that returns HTTP 404
// ("Cannot DELETE /v1/compute/instances/{id}"). Contabo's InstancesApi only
// exposes cancel/create/patch/reinstall/retrieve/upgrade
// (https://api.contabo.com/#tag/Instances — see also
// https://github.com/p-fruck/python-contabo/blob/main/docs/InstancesApi.md,
// and contabo/terraform-provider-contabo's resourceInstanceDelete, which
// also calls CancelInstance). The correct call is
// POST /v1/compute/instances/{id}/cancel with a JSON body (an empty object
// is accepted — Contabo's own Go/Python/Terraform clients send no
// meaningful fields either).
//
// IMPORTANT scale-down semantics caveat: per Contabo's docs
// (https://help.contabo.com/.../how-do-i-cancel-a-service-) cancellation is
// NOT immediate — "[y]our service will remain active until the displayed
// cancellation date," i.e. cancel just schedules removal at the end of the
// current billing period (returned here as data[0].cancelDate, logged for
// visibility). The instance keeps running — and keeps being returned by
// GET /v1/compute/instances, and therefore keeps showing up in ListByTag,
// since its tag assignment is untouched — until that date. CA's
// NodeGroupTargetSize/NodeGroupNodes (internal/provider/size.go) derive
// purely from ListByTag, so immediately after a scale-down the "removed"
// node will still be counted/listed; there's no documented Contabo API for
// a hard, immediate terminate. Fully closing this gap (e.g. also stripping
// the elastic tag on cancel so ListByTag reflects the scale-down instantly)
// needs Delete to know the tag name, which the Client interface
// deliberately doesn't expose today — left as a flagged follow-up rather
// than widening the interface here.
//
// Delete itself retries a 404/5xx cancel response up to cancelRetryAttempts
// times (fixed backoff of cancelRetryInterval between attempts) before
// giving up. This matters most on the rollback path — Create calls Delete
// to clean up an instance whose tag assignment ultimately failed, and a
// cancel call can race the exact same Contabo eventual-consistency window
// that made the tag assignment fail in the first place (404 "resource not
// found") or hit a transient 5xx. A non-retrying cancel there would leak the
// instance exactly like the bug this whole retry chain exists to fix. Any
// give-up after exhausting the retry budget logs the instance id prominently
// so an orphan is traceable (see ca-delete-instance.yml for manual cleanup).
func (c *HTTPClient) Delete(ctx context.Context, id int64) error {
	var lastErr error
	for attempt := 0; attempt < c.cancelRetryAttempts; attempt++ {
		if attempt > 0 {
			if err := sleepCtx(ctx, c.cancelRetryInterval); err != nil {
				return err
			}
		}
		err := c.cancelOnce(ctx, id)
		if err == nil {
			return nil
		}
		lastErr = err
		if !errors.Is(err, errRetryable) {
			return err
		}
		log.Printf("contabo: cancel of instance %d retryable failure (attempt %d/%d): %v", id, attempt+1, c.cancelRetryAttempts, err)
	}
	log.Printf("contabo: cancel of instance %d did NOT succeed after %d attempts — instance %d may be LEAKED/orphaned and needs manual cleanup (see ca-delete-instance.yml): %v", id, c.cancelRetryAttempts, id, lastErr)
	return fmt.Errorf("cancel instance %d: exhausted %d retry attempts: %w", id, c.cancelRetryAttempts, lastErr)
}

// cancelOnce performs a single logical cancel of instance id, including the
// existing transparent 401-token-refresh retry. Any 404/5xx failure is
// wrapped with errRetryable (via isRetryableStatus) so Delete's outer retry
// loop can distinguish it from a hard failure.
func (c *HTTPClient) cancelOnce(ctx context.Context, id int64) error {
	path := fmt.Sprintf("/v1/compute/instances/%d/cancel", id)

	// Helper to perform the actual cancel request
	doDelete := func(tok string) (bool, error) {
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+path, bytes.NewReader([]byte("{}")))
		if err != nil {
			return false, fmt.Errorf("create cancel request: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("x-request-id", newRequestID())

		resp, err := c.hc.Do(req)
		if err != nil {
			log.Printf("contabo: POST %s failed: %v", path, err)
			return false, fmt.Errorf("cancel request failed: %w", err)
		}
		defer resp.Body.Close()

		body, _ := io.ReadAll(resp.Body)
		log.Printf("contabo: POST %s -> %d: %s", path, resp.StatusCode, string(body))

		// On 401, signal to retry with a fresh token
		if resp.StatusCode == http.StatusUnauthorized {
			return true, errors.New("unauthorized")
		}

		// Accept 200/201/204 as success (docs specify 201; tolerate the
		// others in case Contabo's actual behavior differs slightly).
		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated && resp.StatusCode != http.StatusNoContent {
			if isRetryableStatus(resp.StatusCode) {
				return false, fmt.Errorf("cancel request failed with status %d: %s: %w", resp.StatusCode, string(body), errRetryable)
			}
			return false, fmt.Errorf("cancel request failed with status %d: %s", resp.StatusCode, string(body))
		}

		var result struct {
			Data []struct {
				InstanceID int64  `json:"instanceId"`
				CancelDate string `json:"cancelDate"`
			} `json:"data"`
		}
		if err := json.Unmarshal(body, &result); err == nil && len(result.Data) > 0 {
			log.Printf("contabo: instance %d scheduled for cancellation on %s (NOT immediate — remains running/billed until then)", id, result.Data[0].CancelDate)
		}

		return false, nil
	}

	// First attempt
	tok, err := c.token(ctx)
	if err != nil {
		return fmt.Errorf("get token: %w", err)
	}

	shouldRetry, err := doDelete(tok)
	if !shouldRetry {
		return err
	}

	// Retry with fresh token
	c.invalidateToken()
	tok, err = c.token(ctx)
	if err != nil {
		return fmt.Errorf("get token (retry): %w", err)
	}

	_, err = doDelete(tok)
	return err
}
