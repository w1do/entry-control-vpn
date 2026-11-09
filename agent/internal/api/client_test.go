package api

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// newTestClient builds a client pointing at a test server.
func newTestClient(t *testing.T, serverURL string) *Client {
	t.Helper()
	client, err := NewClient(ClientOptions{
		BaseURL:      serverURL,
		Token:        "test-token",
		AgentVersion: "9.9.9",
		Timeout:      2 * time.Second,
		MaxAttempts:  3,
		BackoffBase:  time.Microsecond,
		BackoffMax:   time.Millisecond,
		Sleep:        func(context.Context, time.Duration) error { return nil },
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func TestNewClientValidation(t *testing.T) {
	cases := []ClientOptions{
		{},
		{BaseURL: "panel.example.com"},
		{BaseURL: "ftp://panel.example.com"},
		{BaseURL: "https:///nohost"},
	}
	for _, options := range cases {
		if _, err := NewClient(options); err == nil {
			t.Errorf("expected an error for %+v", options)
		}
	}
}

func TestRegisterAndHeartbeat(t *testing.T) {
	var registerCalls, heartbeatCalls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer test-token" {
			t.Errorf("authorization = %q", got)
		}
		if got := r.Header.Get("User-Agent"); got != "vpn-agent/9.9.9" {
			t.Errorf("user agent = %q", got)
		}
		if got := r.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("content type = %q", got)
		}
		switch r.URL.Path {
		case PathRegister:
			atomic.AddInt32(&registerCalls, 1)
			var req RegisterRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Errorf("decode register: %v", err)
			}
			if req.AgentVersion != "9.9.9" || req.OS != "linux" || len(req.Capabilities) == 0 {
				t.Errorf("register request = %+v", req)
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"server_id":11,"revision":"pending"}`)
		case PathHeartbeat:
			atomic.AddInt32(&heartbeatCalls, 1)
			var req HeartbeatRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Errorf("decode heartbeat: %v", err)
			}
			if req.Revision != 3 || req.Units["xray"] != "active" {
				t.Errorf("heartbeat request = %+v", req)
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"revision":4,"apply_pending":true,"commands":[{"id":"c1","type":"diagnose"}]}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	ctx := context.Background()

	registered, err := client.Register(ctx, &RegisterRequest{AgentVersion: "9.9.9", OS: "linux", Arch: "amd64", Capabilities: []string{"metrics"}})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}
	if registered.ServerID != 11 || int64(registered.Revision) != 0 {
		t.Fatalf("register response = %+v", registered)
	}
	heartbeat, err := client.Heartbeat(ctx, &HeartbeatRequest{Revision: 3, Units: map[string]string{"xray": "active"}})
	if err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}
	if int64(heartbeat.Revision) != 4 || !heartbeat.ApplyPending || len(heartbeat.Commands) != 1 {
		t.Fatalf("heartbeat response = %+v", heartbeat)
	}
	if atomic.LoadInt32(&registerCalls) != 1 || atomic.LoadInt32(&heartbeatCalls) != 1 {
		t.Fatalf("calls = %d %d", registerCalls, heartbeatCalls)
	}
}

func TestGetConfig(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != PathConfig {
			http.NotFound(w, r)
			return
		}
		if got := r.URL.Query().Get("revision"); got != "5" {
			t.Errorf("revision query = %q", got)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	response, err := client.GetConfig(context.Background(), 5)
	if err != nil {
		t.Fatalf("GetConfig: %v", err)
	}
	if response != nil {
		t.Fatalf("expected nil response for 204")
	}
}

func TestGetConfigOK(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"revision":"6","config":{"inbounds":[]},"sha256":"abc"}`)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	response, err := client.GetConfig(context.Background(), 5)
	if err != nil {
		t.Fatalf("GetConfig: %v", err)
	}
	if int64(response.Revision) != 6 || !strings.Contains(string(response.Config), "inbounds") {
		t.Fatalf("response = %+v", response)
	}
}

func TestApplyResultAndCommandResult(t *testing.T) {
	var applyBody, commandBody map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch r.URL.Path {
		case PathApplyResult:
			_ = json.Unmarshal(body, &applyBody)
		case PathCommandResult:
			_ = json.Unmarshal(body, &commandBody)
		case PathUninstall:
			_, _ = io.WriteString(w, `{"ok":true}`)
		default:
			http.NotFound(w, r)
			return
		}
		_, _ = io.WriteString(w, `{}`)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	ctx := context.Background()
	if err := client.PostApplyResult(ctx, &ApplyResult{Revision: 7, Status: StatusApplied, SHA256: "abc"}); err != nil {
		t.Fatalf("PostApplyResult: %v", err)
	}
	if applyBody["status"] != "applied" || applyBody["revision"].(float64) != 7 {
		t.Fatalf("apply body = %v", applyBody)
	}
	if err := client.PostCommandResult(ctx, &CommandResult{CommandID: "c1", Status: "done", Output: "ok"}); err != nil {
		t.Fatalf("PostCommandResult: %v", err)
	}
	if commandBody["command_id"] != "c1" || commandBody["status"] != "done" {
		t.Fatalf("command body = %v", commandBody)
	}
	if err := client.PostUninstall(ctx, UninstallStatus); err != nil {
		t.Fatalf("PostUninstall: %v", err)
	}
}

func TestPostDiagnoseWrapsReport(t *testing.T) {
	var wrapped map[string]json.RawMessage
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &wrapped)
		_, _ = io.WriteString(w, `{}`)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	if err := client.PostDiagnose(context.Background(), json.RawMessage(`{"revision":1}`)); err != nil {
		t.Fatalf("PostDiagnose: %v", err)
	}
	if _, ok := wrapped["report"]; !ok {
		t.Fatalf("diagnose body has no report wrapper: %v", wrapped)
	}
}

func TestMetricsBody(t *testing.T) {
	var raw []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ = io.ReadAll(r.Body)
		_, _ = io.WriteString(w, `{"ok":true,"accepted":true,"commands":[{"id":"c2","type":"restart_services"}]}`)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	batch := &MetricsBatch{
		TS:           time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		Revision:     12,
		AgentVersion: "9.9.9",
		Xray:         XrayStatus{Active: true, UptimeS: 10, Version: "26.11.9"},
		TrafficDelta: Traffic{Up: 100, Down: 200},
		Clients:      []ClientMetrics{{Email: "a@b", Up: 10, Down: 20, Online: true, IPs: []string{"203.0.113.7"}}},
		Connections:  Connections{Accepted15s: 3, Blocked15s: 1, Online: 1},
		Errors:       Errors{Rejected15s: 1, XrayError15s: 0},
		Replay:       true,
	}
	response, err := client.PostMetrics(context.Background(), batch)
	if err != nil {
		t.Fatalf("PostMetrics: %v", err)
	}
	if len(response.Commands) != 1 || response.Commands[0].Type != "restart_services" {
		t.Fatalf("metrics response = %+v", response)
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatalf("decode metrics body: %v", err)
	}
	if decoded["ts"] != "2026-01-01T00:00:00Z" {
		t.Errorf("ts = %v", decoded["ts"])
	}
	if decoded["replay"] != true {
		t.Errorf("replay = %v", decoded["replay"])
	}
	if decoded["agent_version"] != "9.9.9" {
		t.Errorf("agent_version = %v", decoded["agent_version"])
	}
	traffic := decoded["traffic_delta"].(map[string]any)
	if traffic["up"].(float64) != 100 || traffic["down"].(float64) != 200 {
		t.Errorf("traffic_delta = %v", traffic)
	}
	connections := decoded["connections"].(map[string]any)
	if connections["accepted_15s"].(float64) != 3 || connections["blocked_15s"].(float64) != 1 {
		t.Errorf("connections = %v", connections)
	}
}

func TestUnauthorizedIsNotRetried(t *testing.T) {
	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "unauthorized", http.StatusUnauthorized)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	_, err := client.Heartbeat(context.Background(), &HeartbeatRequest{})
	if !errors.Is(err, ErrUnauthorized) {
		t.Fatalf("error = %v, want ErrUnauthorized", err)
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("calls = %d, want 1", got)
	}
}

func TestRetriesTransientErrors(t *testing.T) {
	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if atomic.AddInt32(&calls, 1) < 3 {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		_, _ = io.WriteString(w, `{"revision":1}`)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	response, err := client.Heartbeat(context.Background(), &HeartbeatRequest{})
	if err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}
	if int64(response.Revision) != 1 {
		t.Fatalf("response = %+v", response)
	}
	if got := atomic.LoadInt32(&calls); got != 3 {
		t.Fatalf("calls = %d, want 3", got)
	}
}

func TestRetriesExhausted(t *testing.T) {
	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "boom", http.StatusBadGateway)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	if _, err := client.Heartbeat(context.Background(), &HeartbeatRequest{}); err == nil {
		t.Fatalf("expected an error")
	}
	if got := atomic.LoadInt32(&calls); got != 3 {
		t.Fatalf("calls = %d, want 3", got)
	}
}

func TestHTTPErrorIsNotRetried(t *testing.T) {
	var calls int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		http.Error(w, "unprocessable", http.StatusUnprocessableEntity)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	err := client.PostApplyResult(context.Background(), &ApplyResult{Revision: 1, Status: StatusApplied})
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) || httpErr.StatusCode != http.StatusUnprocessableEntity {
		t.Fatalf("error = %v", err)
	}
	if got := atomic.LoadInt32(&calls); got != 1 {
		t.Fatalf("calls = %d, want 1", got)
	}
}

func TestDownloadArtifact(t *testing.T) {
	payload := []byte("agent-binary-payload")
	sum := sha256.Sum256(payload)
	checksum := hex.EncodeToString(sum[:])
	var mode atomic.Value
	mode.Store("ok")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, PathArtifacts+"/") {
			http.NotFound(w, r)
			return
		}
		switch mode.Load().(string) {
		case "nocsum":
			// no checksum header
		case "badcsum":
			w.Header().Set(artifactChecksumHdr, strings.Repeat("0", 64))
		default:
			w.Header().Set(artifactChecksumHdr, checksum)
		}
		_, _ = w.Write(payload)
	}))
	defer server.Close()
	client := newTestClient(t, server.URL)
	artifact, err := client.DownloadArtifact(context.Background(), "vpn-agent-linux-amd64")
	if err != nil {
		t.Fatalf("DownloadArtifact: %v", err)
	}
	if string(artifact.Data) != string(payload) || artifact.Checksum != checksum {
		t.Fatalf("artifact = %+v", artifact)
	}
	mode.Store("badcsum")
	if _, err := client.DownloadArtifact(context.Background(), "vpn-agent-linux-amd64"); err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("bad checksum error = %v", err)
	}
	mode.Store("nocsum")
	if _, err := client.DownloadArtifact(context.Background(), "vpn-agent-linux-amd64"); err == nil || !strings.Contains(err.Error(), "no X-Checksum-Sha256") {
		t.Fatalf("missing checksum error = %v", err)
	}
	if _, err := client.DownloadArtifact(context.Background(), "../evil"); err == nil {
		t.Fatalf("expected an error for an unsafe artifact name")
	}
}

func TestRevisionJSON(t *testing.T) {
	cases := []struct {
		input string
		want  int64
	}{
		{"5", 5},
		{`"7"`, 7},
		{`"pending"`, 0},
		{`"none"`, 0},
		{`""`, 0},
		{"null", 0},
	}
	for _, tc := range cases {
		var revision Revision
		if err := json.Unmarshal([]byte(tc.input), &revision); err != nil {
			t.Fatalf("unmarshal %s: %v", tc.input, err)
		}
		if int64(revision) != tc.want {
			t.Errorf("unmarshal %s = %d, want %d", tc.input, revision, tc.want)
		}
	}
	encoded, err := json.Marshal(Revision(42))
	if err != nil || string(encoded) != "42" {
		t.Fatalf("marshal revision = %s, %v", encoded, err)
	}
}

func TestParseXrayEmails(t *testing.T) {
	emails := ParseXrayEmails([]byte(`{"users":[{"email":"a@b"},{"email":"c@d","extra":{"email":"a@b"}}]}`))
	if len(emails) != 2 {
		t.Fatalf("emails = %v", emails)
	}
}
