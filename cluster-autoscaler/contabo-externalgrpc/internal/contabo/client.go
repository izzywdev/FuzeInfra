package contabo

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
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
	UserData string
	Tags     []string
}

// Client defines the Contabo API client interface.
type Client interface {
	ListByTag(ctx context.Context, tag string) ([]Instance, error)
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
}

// tagColor is the color assigned to any tag this client creates. Contabo
// requires a 4-7 character hex value on POST /v1/tags; the value itself is
// cosmetic (shown in the Contabo control panel) and has no functional
// effect on tag-assignment/lookup.
const tagColor = "#0A78C3"

// NewClient creates a new Contabo API client.
func NewClient(cfg Config) *HTTPClient {
	return &HTTPClient{
		cfg:      cfg,
		hc:       &http.Client{Timeout: 30 * time.Second},
		tagCache: make(map[string]int64),
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

		instances = append(instances, Instance{
			ID:        inst.InstanceID,
			Name:      inst.DisplayName,
			Status:    inst.Status,
			PrivateIP: privateIP,
			Tags:      []string{tag},
		})
	}

	return instances, nil
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
		return fmt.Errorf("tag-assignment request failed with status %d: %s", resp.StatusCode, string(body))
	}
	return nil
}

// applyTags resolves (creating if necessary) and assigns every tag name in
// `tags` to instanceID, stopping at the first failure.
func (c *HTTPClient) applyTags(ctx context.Context, tok string, instanceID int64, tags []string) error {
	for _, name := range tags {
		tagID, err := c.resolveTagID(ctx, tok, name)
		if err != nil {
			return fmt.Errorf("resolve tag %q: %w", name, err)
		}
		if err := c.assignTag(ctx, tok, tagID, instanceID); err != nil {
			return fmt.Errorf("assign tag %q (id=%d) to instance %d: %w", name, tagID, instanceID, err)
		}
	}
	return nil
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
		createBody := struct {
			DisplayName string  `json:"displayName"`
			ImageID     string  `json:"imageId"`
			ProductID   string  `json:"productId"`
			Region      string  `json:"region"`
			SSHKeys     []int64 `json:"sshKeys,omitempty"`
			UserData    string  `json:"userData"`
		}{
			DisplayName: req.Name,
			ImageID:     req.ImageID,
			ProductID:   req.ProductID,
			Region:      req.Region,
			SSHKeys:     sshKeys,
			UserData:    base64.StdEncoding.EncodeToString([]byte(req.UserData)),
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
		// (best-effort delete) rather than returning it as a success — the
		// instance must end up either tagged, or gone.
		if len(req.Tags) > 0 {
			tagErr := c.applyTags(ctx, tok, inst.InstanceID, req.Tags)
			if tagErr != nil {
				log.Printf("contabo: instance %d (%s) created but tagging failed (%v); rolling back", inst.InstanceID, inst.DisplayName, tagErr)
				if delErr := c.Delete(ctx, inst.InstanceID); delErr != nil {
					log.Printf("contabo: rollback delete of untagged instance %d also failed: %v — instance may be LEAKED, needs manual cleanup", inst.InstanceID, delErr)
					return Instance{}, false, fmt.Errorf("tag instance %d: %w (rollback delete also failed: %v)", inst.InstanceID, tagErr, delErr)
				}
				return Instance{}, false, fmt.Errorf("tag instance %d: %w (instance rolled back)", inst.InstanceID, tagErr)
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
func (c *HTTPClient) Delete(ctx context.Context, id int64) error {
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
