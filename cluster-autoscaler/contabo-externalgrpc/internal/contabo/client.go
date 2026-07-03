package contabo

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
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
	SSHKeyID  int64
	UserData  string
	Tags      []string
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
}

// NewClient creates a new Contabo API client.
func NewClient(cfg Config) *HTTPClient {
	return &HTTPClient{
		cfg: cfg,
		hc:  &http.Client{Timeout: 30 * time.Second},
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
		// Fallback: on error, use a timestamp-free counter (but crypto/rand effectively never fails)
		return fmt.Sprintf("req-%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

// token returns a valid OAuth2 bearer token, refreshing if necessary.
func (c *HTTPClient) token(ctx context.Context) (string, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Return cached token if not expired
	if c.tok != "" && time.Now().Before(c.tokExpiry) {
		return c.tok, nil
	}

	// Determine auth endpoint
	authURL := c.cfg.AuthURL
	if authURL == "" {
		authURL = c.cfg.BaseURL
	}

	// Build request body with real credentials
	credBody := struct {
		ClientID     string `json:"clientId"`
		ClientSecret string `json:"clientSecret"`
		Username     string `json:"username"`
		Password     string `json:"password"`
	}{
		ClientID:     c.cfg.ClientID,
		ClientSecret: c.cfg.ClientSecret,
		Username:     c.cfg.User,
		Password:     c.cfg.Pass,
	}
	reqBody, err := json.Marshal(credBody)
	if err != nil {
		return "", fmt.Errorf("marshal token request: %w", err)
	}

	// POST password grant to /oauth/token
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, authURL+"/oauth/token", bytes.NewReader(reqBody))
	if err != nil {
		return "", fmt.Errorf("create token request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-request-id", newRequestID())

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

// ListByTag lists all instances with the given tag.
func (c *HTTPClient) ListByTag(ctx context.Context, tag string) ([]Instance, error) {
	tok, err := c.token(ctx)
	if err != nil {
		return nil, fmt.Errorf("get token: %w", err)
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
		return nil, fmt.Errorf("instances request failed: %w", err)
	}
	defer resp.Body.Close()

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
			Tags []struct {
				Name string `json:"name"`
			} `json:"tags"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode instances response: %w", err)
	}

	// Filter instances by tag
	var instances []Instance
	for _, inst := range result.Data {
		// Check if instance has the requested tag
		hasTag := false
		var tags []string
		for _, t := range inst.Tags {
			tags = append(tags, t.Name)
			if t.Name == tag {
				hasTag = true
			}
		}

		if !hasTag {
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
			Tags:      tags,
		})
	}

	return instances, nil
}

// Create creates a new instance.
func (c *HTTPClient) Create(ctx context.Context, req CreateReq) (Instance, error) {
	// Helper to perform the actual create request
	doCreate := func(tok string) (Instance, bool, error) {
		// Prepare the request body
		createBody := struct {
			DisplayName string  `json:"displayName"`
			ImageID     string  `json:"imageId"`
			ProductID   string  `json:"productId"`
			Region      string  `json:"region"`
			SSHKeys     []int64 `json:"sshKeys"`
			UserData    string  `json:"userData"`
		}{
			DisplayName: req.Name,
			ImageID:     req.ImageID,
			ProductID:   req.ProductID,
			Region:      req.Region,
			SSHKeys:     []int64{req.SSHKeyID},
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

		// Apply tags via a separate API call if tags are provided
		if len(req.Tags) > 0 {
			tok, err := c.token(ctx)
			if err != nil {
				// Log but don't fail if tag assignment fails
				log.Printf("warning: failed to refresh token for tag assignment: %v", err)
			} else {
				// POST /v1/compute/instances/{id}/tag-assignments
				tagBody := struct {
					Tags []string `json:"tags"`
				}{
					Tags: req.Tags,
				}
				tagBodyBytes, err := json.Marshal(tagBody)
				if err != nil {
					log.Printf("warning: failed to marshal tag request: %v", err)
				} else {
					tagReq, err := http.NewRequestWithContext(ctx, http.MethodPost,
						fmt.Sprintf("%s/v1/compute/instances/%d/tag-assignments", c.cfg.BaseURL, inst.InstanceID),
						bytes.NewReader(tagBodyBytes))
					if err != nil {
						log.Printf("warning: failed to create tag request: %v", err)
					} else {
						tagReq.Header.Set("Authorization", "Bearer "+tok)
						tagReq.Header.Set("Content-Type", "application/json")
						tagReq.Header.Set("x-request-id", newRequestID())
						tagResp, err := c.hc.Do(tagReq)
						if err != nil {
							log.Printf("warning: tag assignment request failed: %v", err)
						} else {
							if tagResp.StatusCode < 200 || tagResp.StatusCode >= 300 {
								body, _ := io.ReadAll(tagResp.Body)
								log.Printf("warning: tag assignment failed with status %d: %s", tagResp.StatusCode, string(body))
							}
							tagResp.Body.Close()
						}
					}
				}
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

// Delete deletes an instance.
func (c *HTTPClient) Delete(ctx context.Context, id int64) error {
	// Helper to perform the actual delete request
	doDelete := func(tok string) (bool, error) {
		// DELETE /v1/compute/instances/{id}
		req, err := http.NewRequestWithContext(ctx, http.MethodDelete,
			fmt.Sprintf("%s/v1/compute/instances/%d", c.cfg.BaseURL, id), nil)
		if err != nil {
			return false, fmt.Errorf("create delete request: %w", err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("x-request-id", newRequestID())

		resp, err := c.hc.Do(req)
		if err != nil {
			return false, fmt.Errorf("delete request failed: %w", err)
		}
		defer resp.Body.Close()

		// On 401, signal to retry with a fresh token
		if resp.StatusCode == http.StatusUnauthorized {
			return true, errors.New("unauthorized")
		}

		// Accept both 200 and 204 as success
		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusNoContent {
			body, _ := io.ReadAll(resp.Body)
			return false, fmt.Errorf("delete request failed with status %d: %s", resp.StatusCode, string(body))
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
