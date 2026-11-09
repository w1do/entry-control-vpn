package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"log/slog"
)

// ErrUnauthorized is returned when the panel rejects the agent token (HTTP
// 401/403). The agent must keep running on the last applied configuration and
// must not disclose any configuration.
var ErrUnauthorized = errors.New("api: unauthorized")

// ErrNotFound is returned for HTTP 404 answers.
var ErrNotFound = errors.New("api: not found")

const (
	maxJSONResponseBytes = 8 << 20
	maxArtifactBytes     = 64 << 20
	artifactChecksumHdr  = "X-Checksum-Sha256"
)

// HTTPError is a non-retryable HTTP failure.
type HTTPError struct {
	StatusCode int
	Body       string
}

// Error implements error.
func (e *HTTPError) Error() string {
	if e.Body == "" {
		return fmt.Sprintf("api: unexpected HTTP status %d", e.StatusCode)
	}
	return fmt.Sprintf("api: HTTP %d: %s", e.StatusCode, e.Body)
}

// ClientOptions configures a Client.
type ClientOptions struct {
	BaseURL       string
	Token         string
	AgentVersion  string
	Timeout       time.Duration
	MaxAttempts   int
	BackoffBase   time.Duration
	BackoffMax    time.Duration
	TLSSkipVerify bool
	Transport     http.RoundTripper
	Logger        *slog.Logger
	// Sleep overrides the retry sleep function (tests).
	Sleep func(ctx context.Context, d time.Duration) error
}

// Client is the panel HTTP client. It is safe for concurrent use.
type Client struct {
	baseURL      string
	token        string
	agentVersion string
	httpClient   *http.Client
	maxAttempts  int
	backoffBase  time.Duration
	backoffMax   time.Duration
	sleep        func(ctx context.Context, d time.Duration) error
	logger       *slog.Logger
}

// NewClient validates the options and returns a panel client.
func NewClient(o ClientOptions) (*Client, error) {
	raw := strings.TrimSpace(o.BaseURL)
	if raw == "" {
		return nil, errors.New("api: panel base URL is empty")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("api: invalid panel URL: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, fmt.Errorf("api: panel URL must use http or https, got %q", u.Scheme)
	}
	if u.Host == "" {
		return nil, errors.New("api: panel URL has no host")
	}
	timeout := o.Timeout
	if timeout <= 0 {
		timeout = 15 * time.Second
	}
	maxAttempts := o.MaxAttempts
	if maxAttempts <= 0 {
		maxAttempts = 3
	}
	backoffBase := o.BackoffBase
	if backoffBase <= 0 {
		backoffBase = 500 * time.Millisecond
	}
	backoffMax := o.BackoffMax
	if backoffMax <= 0 {
		backoffMax = 10 * time.Second
	}
	sleepFn := o.Sleep
	if sleepFn == nil {
		sleepFn = sleepContext
	}
	logger := o.Logger
	if logger == nil {
		logger = slog.Default()
	}
	transport := o.Transport
	if transport == nil {
		base, ok := http.DefaultTransport.(*http.Transport)
		if ok {
			cloned := base.Clone()
			if o.TLSSkipVerify {
				if cloned.TLSClientConfig == nil {
					cloned.TLSClientConfig = &tls.Config{}
				} else {
					cloned.TLSClientConfig = cloned.TLSClientConfig.Clone()
				}
				cloned.TLSClientConfig.InsecureSkipVerify = true
			}
			transport = cloned
		} else {
			transport = http.DefaultTransport
		}
	}
	return &Client{
		baseURL:      strings.TrimRight(raw, "/"),
		token:        o.Token,
		agentVersion: o.AgentVersion,
		httpClient:   &http.Client{Timeout: timeout, Transport: transport},
		maxAttempts:  maxAttempts,
		backoffBase:  backoffBase,
		backoffMax:   backoffMax,
		sleep:        sleepFn,
		logger:       logger,
	}, nil
}

// Register registers the node in the panel.
func (c *Client) Register(ctx context.Context, req *RegisterRequest) (*RegisterResponse, error) {
	var resp RegisterResponse
	if _, err := c.do(ctx, http.MethodPost, PathRegister, nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// Heartbeat reports node liveness and fetches pending commands.
func (c *Client) Heartbeat(ctx context.Context, req *HeartbeatRequest) (*HeartbeatResponse, error) {
	var resp HeartbeatResponse
	if _, err := c.do(ctx, http.MethodPost, PathHeartbeat, nil, req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// GetConfig fetches the configuration for a revision. A nil result with a nil
// error means the panel answered 204 (no changes).
func (c *Client) GetConfig(ctx context.Context, revision int64) (*ConfigResponse, error) {
	q := url.Values{}
	q.Set("revision", strconv.FormatInt(revision, 10))
	var resp ConfigResponse
	status, err := c.do(ctx, http.MethodGet, PathConfig, q, nil, &resp)
	if err != nil {
		return nil, err
	}
	if status == http.StatusNoContent {
		return nil, nil
	}
	if len(resp.Config) == 0 {
		return nil, fmt.Errorf("api: config response for revision %d is empty", resp.Revision)
	}
	return &resp, nil
}

// PostApplyResult reports the outcome of applying one revision.
func (c *Client) PostApplyResult(ctx context.Context, res *ApplyResult) error {
	_, err := c.do(ctx, http.MethodPost, PathApplyResult, nil, res, nil)
	return err
}

// PostMetrics sends one metrics batch.
func (c *Client) PostMetrics(ctx context.Context, batch *MetricsBatch) (*MetricsResponse, error) {
	var resp MetricsResponse
	if _, err := c.do(ctx, http.MethodPost, PathMetrics, nil, batch, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// PostDiagnose sends a diagnostic report.
func (c *Client) PostDiagnose(ctx context.Context, report json.RawMessage) error {
	_, err := c.do(ctx, http.MethodPost, PathDiagnose, nil, &DiagnoseRequest{Report: report}, nil)
	return err
}

// PostUninstall reports that the node cleanup has completed.
func (c *Client) PostUninstall(ctx context.Context, status string) error {
	_, err := c.do(ctx, http.MethodPost, PathUninstall, nil, &UninstallRequest{Status: status}, nil)
	return err
}

// PostCommandResult reports the outcome of one panel command.
func (c *Client) PostCommandResult(ctx context.Context, res *CommandResult) error {
	_, err := c.do(ctx, http.MethodPost, PathCommandResult, nil, res, nil)
	return err
}

// DownloadArtifact downloads an agent artifact and verifies its SHA-256
// checksum from the X-Checksum-Sha256 header.
func (c *Client) DownloadArtifact(ctx context.Context, name string) (*Artifact, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return nil, errors.New("api: artifact name is empty")
	}
	if strings.ContainsAny(name, "/\\") {
		return nil, fmt.Errorf("api: invalid artifact name %q", name)
	}
	httpResp, err := c.execute(ctx, http.MethodGet, PathArtifacts+"/"+url.PathEscape(name), nil, nil)
	if err != nil {
		return nil, err
	}
	if httpResp.Status == http.StatusUnauthorized || httpResp.Status == http.StatusForbidden {
		return nil, fmt.Errorf("%w: artifact %s returned HTTP %d", ErrUnauthorized, name, httpResp.Status)
	}
	if httpResp.Status == http.StatusNotFound {
		return nil, fmt.Errorf("%w: artifact %s", ErrNotFound, name)
	}
	if httpResp.Status < 200 || httpResp.Status >= 300 {
		return nil, &HTTPError{StatusCode: httpResp.Status, Body: truncateBody(httpResp.Body)}
	}
	want := strings.ToLower(strings.TrimSpace(httpResp.Header.Get(artifactChecksumHdr)))
	if want == "" {
		return nil, fmt.Errorf("api: artifact %s response has no %s header", name, artifactChecksumHdr)
	}
	sum := sha256.Sum256(httpResp.Body)
	got := hex.EncodeToString(sum[:])
	if got != want {
		return nil, fmt.Errorf("api: artifact %s checksum mismatch: want %s, got %s", name, want, got)
	}
	return &Artifact{Name: name, Data: httpResp.Body, Checksum: got}, nil
}

// do sends a JSON request and decodes a JSON answer.
func (c *Client) do(ctx context.Context, method, path string, query url.Values, body, out any) (int, error) {
	var payload []byte
	if body != nil {
		var err error
		payload, err = json.Marshal(body)
		if err != nil {
			return 0, fmt.Errorf("api: marshal %s request: %w", path, err)
		}
	}
	httpResp, err := c.execute(ctx, method, path, query, payload)
	if err != nil {
		return 0, err
	}
	switch {
	case httpResp.Status >= 200 && httpResp.Status < 300:
		if out != nil && len(httpResp.Body) > 0 {
			if err := json.Unmarshal(httpResp.Body, out); err != nil {
				return httpResp.Status, fmt.Errorf("api: decode %s response: %w", path, err)
			}
		}
		return httpResp.Status, nil
	case httpResp.Status == http.StatusUnauthorized || httpResp.Status == http.StatusForbidden:
		return httpResp.Status, fmt.Errorf("%w: %s %s returned HTTP %d", ErrUnauthorized, method, path, httpResp.Status)
	case httpResp.Status == http.StatusNotFound:
		return httpResp.Status, fmt.Errorf("%w: %s %s", ErrNotFound, method, path)
	default:
		return httpResp.Status, &HTTPError{StatusCode: httpResp.Status, Body: truncateBody(httpResp.Body)}
	}
}

// httpResponse is a raw HTTP answer.
type httpResponse struct {
	Status int
	Body   []byte
	Header http.Header
}

// execute performs one HTTP call with retries on transient failures.
func (c *Client) execute(ctx context.Context, method, path string, query url.Values, payload []byte) (*httpResponse, error) {
	target := c.baseURL + path
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	attempts := c.maxAttempts
	if attempts < 1 {
		attempts = 1
	}
	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		resp, err := c.attempt(ctx, method, target, path, payload)
		if err != nil {
			lastErr = err
			if attempt >= attempts {
				break
			}
			if c.logger != nil {
				c.logger.Debug("panel request failed, retrying", "method", method, "path", path, "attempt", attempt, "error", err)
			}
			if serr := c.sleep(ctx, c.backoff(attempt)); serr != nil {
				return nil, serr
			}
			continue
		}
		if resp.Status == http.StatusTooManyRequests || resp.Status >= 500 {
			lastErr = &HTTPError{StatusCode: resp.Status, Body: truncateBody(resp.Body)}
			if attempt >= attempts {
				return resp, lastErr
			}
			if c.logger != nil {
				c.logger.Debug("panel answered with retryable status", "path", path, "status", resp.Status, "attempt", attempt)
			}
			if serr := c.sleep(ctx, c.backoff(attempt)); serr != nil {
				return nil, serr
			}
			continue
		}
		return resp, nil
	}
	return nil, fmt.Errorf("api: %s %s failed after %d attempts: %w", method, path, attempts, lastErr)
}

// attempt performs a single HTTP request.
func (c *Client) attempt(ctx context.Context, method, target, path string, payload []byte) (*httpResponse, error) {
	var reader io.Reader
	if payload != nil {
		reader = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, method, target, reader)
	if err != nil {
		return nil, fmt.Errorf("api: build request for %s: %w", path, err)
	}
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	if c.agentVersion != "" {
		req.Header.Set("User-Agent", "vpn-agent/"+c.agentVersion)
	} else {
		req.Header.Set("User-Agent", "vpn-agent")
	}
	req.Header.Set("Accept", "application/json")
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("api: %s %s: %w", method, path, err)
	}
	defer func() { _ = resp.Body.Close() }()
	limit := int64(maxJSONResponseBytes)
	if path == PathArtifacts || strings.HasPrefix(path, PathArtifacts+"/") {
		limit = maxArtifactBytes
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, limit))
	if err != nil {
		return nil, fmt.Errorf("api: read %s response: %w", path, err)
	}
	return &httpResponse{Status: resp.StatusCode, Body: body, Header: resp.Header}, nil
}

// backoff computes the retry delay for the given attempt (full jitter).
func (c *Client) backoff(attempt int) time.Duration {
	d := c.backoffBase
	for i := 1; i < attempt; i++ {
		d *= 2
		if d >= c.backoffMax {
			d = c.backoffMax
			break
		}
	}
	if d > c.backoffMax {
		d = c.backoffMax
	}
	if d <= 0 {
		return 0
	}
	half := d / 2
	if half <= 0 {
		return d
	}
	return half + time.Duration(rand.Int63n(int64(half)+1))
}

// sleepContext sleeps for d or until ctx is cancelled.
func sleepContext(ctx context.Context, d time.Duration) error {
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

// truncateBody shortens a response body for error messages.
func truncateBody(body []byte) string {
	const max = 512
	s := strings.TrimSpace(string(body))
	if len(s) > max {
		return s[:max] + "..."
	}
	return s
}
