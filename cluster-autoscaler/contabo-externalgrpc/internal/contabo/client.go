package contabo

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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
	cfg        Config
	hc         *http.Client
	mu         sync.Mutex
	tok        string
	tokExpiry  time.Time
}

// NewClient creates a new Contabo API client.
func NewClient(cfg Config) *HTTPClient {
	return &HTTPClient{
		cfg: cfg,
		hc:  &http.Client{Timeout: 30 * time.Second},
	}
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
	req.Header.Set("x-request-id", "auth-token")

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
	req.Header.Set("x-request-id", "list-instances")

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
			InstanceID int64 `json:"instanceId"`
			DisplayName string `json:"displayName"`
			Status     string `json:"status"`
			Addresses  struct {
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

// Create creates a new instance (stub for Task 2).
func (c *HTTPClient) Create(ctx context.Context, req CreateReq) (Instance, error) {
	return Instance{}, errors.New("not implemented")
}

// Delete deletes an instance (stub for Task 2).
func (c *HTTPClient) Delete(ctx context.Context, id int64) error {
	return errors.New("not implemented")
}
