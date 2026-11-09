package runner

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"vpn-agent/internal/api"
	"vpn-agent/internal/apply"
	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/metrics"
	"vpn-agent/internal/state"
	"vpn-agent/internal/uninstall"
)

const testToken = "test-token"

// fakePanel is an in-memory control panel.
type fakePanel struct {
	mu sync.Mutex

	server *httptest.Server

	registerCalls  int
	heartbeatCalls int
	configCalls    int

	revision    int64
	configJSON  []byte
	serveConfig bool

	heartbeatResp   api.HeartbeatResponse
	heartbeatStatus int
	metricsStatus   int
	registerStatus  int

	lastHeartbeat api.HeartbeatRequest
	applyResults  []api.ApplyResult
	metrics       []api.MetricsBatch
	diagnose      []json.RawMessage
	commandResult []api.CommandResult
	uninstalls    []string
	artifacts     map[string][]byte
}

func newFakePanel(t *testing.T) *fakePanel {
	t.Helper()
	panel := &fakePanel{artifacts: map[string][]byte{}}
	panel.server = httptest.NewServer(http.HandlerFunc(panel.handle))
	t.Cleanup(panel.server.Close)
	return panel
}

func (p *fakePanel) handle(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer "+testToken {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	body, _ := io.ReadAll(r.Body)
	p.mu.Lock()
	defer p.mu.Unlock()
	switch r.URL.Path {
	case api.PathRegister:
		p.registerCalls++
		if p.registerStatus != 0 {
			http.Error(w, "register failed", p.registerStatus)
			return
		}
		_, _ = io.WriteString(w, `{"server_id":1,"revision":"pending"}`)
	case api.PathHeartbeat:
		p.heartbeatCalls++
		if p.heartbeatStatus != 0 {
			http.Error(w, "heartbeat failed", p.heartbeatStatus)
			return
		}
		_ = json.Unmarshal(body, &p.lastHeartbeat)
		response := p.heartbeatResp
		response.Revision = api.Revision(p.revision)
		_ = json.NewEncoder(w).Encode(response)
	case api.PathConfig:
		p.configCalls++
		requested := r.URL.Query().Get("revision")
		if !p.serveConfig || requested == fmt.Sprintf("%d", p.revision) {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		sum := sha256.Sum256(p.configJSON)
		response := api.ConfigResponse{
			Revision: api.Revision(p.revision),
			Config:   p.configJSON,
			SHA256:   hex.EncodeToString(sum[:]),
		}
		_ = json.NewEncoder(w).Encode(response)
	case api.PathApplyResult:
		var result api.ApplyResult
		_ = json.Unmarshal(body, &result)
		p.applyResults = append(p.applyResults, result)
		_, _ = io.WriteString(w, `{"ok":true}`)
	case api.PathMetrics:
		if p.metricsStatus != 0 {
			http.Error(w, "metrics failed", p.metricsStatus)
			return
		}
		var batch api.MetricsBatch
		_ = json.Unmarshal(body, &batch)
		p.metrics = append(p.metrics, batch)
		_, _ = io.WriteString(w, `{"ok":true,"accepted":true,"commands":[]}`)
	case api.PathDiagnose:
		var request api.DiagnoseRequest
		_ = json.Unmarshal(body, &request)
		p.diagnose = append(p.diagnose, request.Report)
		_, _ = io.WriteString(w, `{}`)
	case api.PathCommandResult:
		var result api.CommandResult
		_ = json.Unmarshal(body, &result)
		p.commandResult = append(p.commandResult, result)
		_, _ = io.WriteString(w, `{}`)
	case api.PathUninstall:
		var request api.UninstallRequest
		_ = json.Unmarshal(body, &request)
		p.uninstalls = append(p.uninstalls, request.Status)
		_, _ = io.WriteString(w, `{}`)
	default:
		if name, ok := artifactName(r.URL.Path); ok {
			payload, exists := p.artifacts[name]
			if !exists {
				http.NotFound(w, r)
				return
			}
			sum := sha256.Sum256(payload)
			w.Header().Set("X-Checksum-Sha256", hex.EncodeToString(sum[:]))
			_, _ = w.Write(payload)
			return
		}
		http.NotFound(w, r)
	}
}

// artifactName extracts the artifact name from a request path.
func artifactName(path string) (string, bool) {
	prefix := api.PathArtifacts + "/"
	if len(path) <= len(prefix) || path[:len(prefix)] != prefix {
		return "", false
	}
	return path[len(prefix):], true
}

// harness wires a runner against a fake panel and fake node tools.
type harness struct {
	t    *testing.T
	dir  string
	base string

	panel    *fakePanel
	cfg      *config.Config
	paths    config.Paths
	store    *state.Store
	runner   *Runner
	xray     string
	ctl      string
	stats    string
	calls    string
	listener net.Listener
	port     int

	selfRestarts   int
	selfStops      int
	uninstallCalls int
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	h := &harness{t: t, dir: t.TempDir()}
	h.base = filepath.Join(h.dir, "config", "vpn")
	h.panel = newFakePanel(t)
	h.paths = config.New(h.base).Paths()
	for _, dir := range []string{h.paths.BinDir, h.paths.EtcDir, h.paths.LogDir, h.paths.StateDir} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	h.calls = filepath.Join(h.dir, "calls.log")
	h.stats = filepath.Join(h.dir, "stats.json")
	h.writeStats(0, 0)
	h.writeFile(h.stats, `{"stat":[]}`)

	// Fake xray.
	h.xray = filepath.Join(h.dir, "xray")
	xrayScript := "#!/bin/sh\n" +
		"echo \"xray $*\" >> " + h.calls + "\n" +
		"if [ \"$1\" = \"run\" ]; then\n" +
		"  if [ -f " + filepath.Join(h.dir, "test-exit") + " ]; then exit $(cat " + filepath.Join(h.dir, "test-exit") + "); fi\n" +
		"  echo 'Configuration OK.'\n" +
		"  exit 0\n" +
		"fi\n" +
		"if [ \"$1\" = \"version\" ]; then echo 'Xray 26.11.9 (Xray, Penetrates Everything.)'; exit 0; fi\n" +
		"if [ \"$1\" = \"api\" ]; then\n" +
		"  case \"$2\" in\n" +
		"    statsquery) cat " + h.stats + " ;;\n" +
		"    statssys) echo '{\"NumGoroutine\":5,\"Uptime\":1}' ;;\n" +
		"    statsonlineiplist) echo '{\"users\":[{\"email\":\"client-1@vpn\",\"ips\":[\"203.0.113.7\"]}]}' ;;\n" +
		"    statsgetallonlineusers) echo '{}' ;;\n" +
		"    inbounduser) echo '{\"users\":[{\"email\":\"client-1@vpn\"}]}' ;;\n" +
		"  esac\n" +
		"  exit 0\n" +
		"fi\n" +
		"exit 1\n"
	if err := os.WriteFile(h.xray, []byte(xrayScript), 0o755); err != nil {
		t.Fatal(err)
	}

	// Fake systemctl.
	h.ctl = filepath.Join(h.dir, "systemctl")
	ctlScript := "#!/bin/sh\n" +
		"echo \"systemctl $*\" >> " + h.calls + "\n" +
		"if [ \"$1\" = \"show\" ]; then\n" +
		"  echo 'LoadState=loaded'\n" +
		"  echo 'ActiveState=active'\n" +
		"  echo 'SubState=running'\n" +
		"  echo 'MainPID=777'\n" +
		"  echo 'ActiveEnterTimestampMonotonic=1000000'\n" +
		"  exit 0\n" +
		"fi\n" +
		"if [ \"$1\" = \"is-active\" ]; then echo active; exit 0; fi\n" +
		"exit 0\n"
	if err := os.WriteFile(h.ctl, []byte(ctlScript), 0o755); err != nil {
		t.Fatal(err)
	}

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	h.listener = listener
	h.port = listener.Addr().(*net.TCPAddr).Port
	t.Cleanup(func() { _ = listener.Close() })

	h.cfg = config.New(h.base)
	h.cfg.PanelURL = h.panel.server.URL
	h.cfg.Token = testToken
	h.cfg.ServerID = 1
	h.cfg.XrayBinary = h.xray
	h.cfg.SystemctlBin = h.ctl
	h.cfg.XrayUnit = "xray.service"
	h.cfg.AgentUnit = "vpn-agent.service"
	h.cfg.UnitDir = filepath.Join(h.dir, "units")
	h.cfg.HealthTimeoutS = 2
	h.cfg.ApplyTestTimeoutS = 5
	h.cfg.ApplyRestartTimeoutS = 5
	if err := h.cfg.Finalize(); err != nil {
		t.Fatalf("config.Finalize: %v", err)
	}
	h.paths = h.cfg.Paths()

	store, err := state.Open(h.paths.StateFile)
	if err != nil {
		t.Fatal(err)
	}
	h.store = store

	client, err := api.NewClient(api.ClientOptions{
		BaseURL:      h.panel.server.URL,
		Token:        testToken,
		AgentVersion: "test-version",
		Timeout:      2 * time.Second,
		MaxAttempts:  2,
		BackoffBase:  time.Microsecond,
		BackoffMax:   time.Millisecond,
		Sleep:        func(context.Context, time.Duration) error { return nil },
		Logger:       slog.New(slog.NewTextHandler(io.Discard, nil)),
	})
	if err != nil {
		t.Fatal(err)
	}
	probe := metrics.NewXrayProbe(execx.OSRunner{}, h.xray, "127.0.0.1:10085", filepath.Join(h.base, "share"), nil)
	applier := apply.New(apply.Options{
		Paths:              h.paths,
		Exec:               execx.OSRunner{},
		XrayBin:            h.xray,
		SystemctlBin:       h.ctl,
		XrayUnit:           "xray.service",
		XrayAPIAddr:        "127.0.0.1:10085",
		AssetDir:           filepath.Join(h.base, "share"),
		Probe:              probe,
		HealthTimeout:      2 * time.Second,
		HealthPollInterval: 20 * time.Millisecond,
		TestTimeout:        5 * time.Second,
		RestartTimeout:     5 * time.Second,
	})
	collector := metrics.NewCollector(metrics.Options{
		Paths:        h.paths,
		Probe:        probe,
		Runner:       execx.OSRunner{},
		SystemctlBin: h.ctl,
		XrayUnit:     "xray.service",
		AgentUnit:    "vpn-agent.service",
		OnlineWindow: 2 * time.Minute,
		AgentVersion: "test-version",
		ProcUptime:   func() (float64, error) { return 100, nil },
		Now:          func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) },
	})
	now := func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) }
	agent, err := New(Options{
		Config:  h.cfg,
		Client:  client,
		Store:   store,
		Applier: applier,
		Exec:    execx.OSRunner{},
		Metrics: collector,
		Buffer:  metrics.NewBuffer(h.paths.MetricsBuffer, 24*time.Hour),
		Probe:   probe,
		Logger:  slog.New(slog.NewTextHandler(io.Discard, &slog.HandlerOptions{Level: slog.LevelDebug})),
		Intervals: Intervals{
			Heartbeat:   20 * time.Millisecond,
			ConfigPoll:  20 * time.Millisecond,
			Metrics:     20 * time.Millisecond,
			RetryMin:    5 * time.Millisecond,
			RetryMax:    10 * time.Millisecond,
			AuthBackoff: 50 * time.Millisecond,
		},
		Now:          now,
		ProcUptime:   func() (float64, error) { return 100, nil },
		Version:      "test-version",
		ArtifactName: "vpn-agent-linux-amd64",
		Uninstall: func(context.Context) (uninstall.Report, error) {
			h.uninstallCalls++
			return uninstall.Report{BaseDir: h.base, Steps: []uninstall.Step{{Name: "test", Status: "ok"}}}, nil
		},
		SelfRestart: func(context.Context) error {
			h.selfRestarts++
			return nil
		},
		StopSelf: func(context.Context) error {
			h.selfStops++
			return nil
		},
	})
	if err != nil {
		t.Fatalf("runner.New: %v", err)
	}
	h.runner = agent
	return h
}

// writeFile writes a file creating parent directories.
func (h *harness) writeFile(path, content string) {
	h.t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		h.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		h.t.Fatal(err)
	}
}

// writeStats updates the fake xray statistics.
func (h *harness) writeStats(up, down int64) {
	h.writeFile(h.stats, fmt.Sprintf(
		`{"stat":[{"name":"user>>>client-1@vpn>>>traffic>>>uplink","value":%d},{"name":"user>>>client-1@vpn>>>traffic>>>downlink","value":%d}]}`,
		up, down))
}

// candidateConfig returns the panel configuration for the harness port.
func (h *harness) candidateConfig() []byte {
	return []byte(fmt.Sprintf(
		`{"log":{"loglevel":"warning"},"inbounds":[{"tag":"vless-in","listen":"127.0.0.1","port":%d,"protocol":"vless","settings":{"decryption":"none","clients":[{"id":"11111111-1111-1111-1111-111111111111","email":"client-1@vpn"}]}}],"outbounds":[{"tag":"direct","protocol":"freedom"}]}`,
		h.port))
}

// deliverConfig makes the panel serve a config revision.
func (h *harness) deliverConfig(revision int64) {
	h.panel.mu.Lock()
	h.panel.revision = revision
	h.panel.configJSON = h.candidateConfig()
	h.panel.serveConfig = true
	h.panel.mu.Unlock()
}

// appendAccessLog appends access log lines.
func (h *harness) appendAccessLog(lines ...string) {
	f, err := os.OpenFile(h.paths.AccessLog, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		h.t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	for _, line := range lines {
		if _, err := f.WriteString(line + "\n"); err != nil {
			h.t.Fatal(err)
		}
	}
}

func TestRegisterOnce(t *testing.T) {
	h := newHarness(t)
	if err := h.runner.RegisterOnce(context.Background()); err != nil {
		t.Fatalf("RegisterOnce: %v", err)
	}
	h.panel.mu.Lock()
	calls := h.panel.registerCalls
	h.panel.mu.Unlock()
	if calls != 1 {
		t.Fatalf("register calls = %d", calls)
	}
	if h.store.Snapshot().LastRegisterAt == nil {
		t.Fatalf("registration time was not persisted")
	}
}

func TestConfigPollAppliesRevision(t *testing.T) {
	h := newHarness(t)
	h.deliverConfig(3)
	if err := h.runner.ConfigPollOnce(context.Background()); err != nil {
		t.Fatalf("ConfigPollOnce: %v", err)
	}
	snap := h.store.Snapshot()
	if snap.Revision != 3 {
		t.Fatalf("state revision = %d", snap.Revision)
	}
	if snap.PendingApply != nil {
		t.Fatalf("pending apply was not cleared: %+v", snap.PendingApply)
	}
	if _, err := os.Stat(h.paths.XrayConfig); err != nil {
		t.Fatalf("config was not installed: %v", err)
	}
	h.panel.mu.Lock()
	results := append([]api.ApplyResult(nil), h.panel.applyResults...)
	h.panel.mu.Unlock()
	if len(results) != 1 || results[0].Status != api.StatusApplied || results[0].Revision != 3 {
		t.Fatalf("apply results = %+v", results)
	}
	if results[0].SHA256 == "" {
		t.Errorf("apply result has no sha256")
	}
	if snap.ConfigSHA256 != results[0].SHA256 {
		t.Errorf("state sha = %q, result sha = %q", snap.ConfigSHA256, results[0].SHA256)
	}
}

func TestConfigPollReportsApplyFailure(t *testing.T) {
	h := newHarness(t)
	h.deliverConfig(4)
	if err := os.WriteFile(filepath.Join(h.dir, "test-exit"), []byte("23"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := h.runner.ConfigPollOnce(context.Background()); err != nil {
		t.Fatalf("ConfigPollOnce: %v", err)
	}
	snap := h.store.Snapshot()
	if snap.Revision != 0 {
		t.Fatalf("failed revision must not be recorded, got %d", snap.Revision)
	}
	h.panel.mu.Lock()
	results := append([]api.ApplyResult(nil), h.panel.applyResults...)
	h.panel.mu.Unlock()
	if len(results) != 1 || results[0].Status != api.StatusFailed || results[0].Revision != 4 {
		t.Fatalf("apply results = %+v", results)
	}
	if !strings.Contains(results[0].Error, "xray -test") {
		t.Errorf("error = %q", results[0].Error)
	}
	if results[0].LogTail == "" {
		t.Errorf("failure must include a log tail")
	}
}

func TestHeartbeatPayload(t *testing.T) {
	h := newHarness(t)
	if err := h.store.Update(func(s *state.State) error {
		s.Revision = 9
		s.ConfigSHA256 = "deadbeef"
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	h.panel.mu.Lock()
	h.panel.revision = 9
	h.panel.heartbeatResp = api.HeartbeatResponse{ApplyPending: false}
	h.panel.mu.Unlock()
	if err := h.runner.HeartbeatOnce(context.Background()); err != nil {
		t.Fatalf("HeartbeatOnce: %v", err)
	}
	h.panel.mu.Lock()
	request := h.panel.lastHeartbeat
	h.panel.mu.Unlock()
	if request.Revision != 9 || request.ConfigSHA256 != "deadbeef" {
		t.Fatalf("heartbeat request = %+v", request)
	}
	if request.Units["xray"] != "active" || request.Units["agent"] != "active" {
		t.Fatalf("units = %v", request.Units)
	}
	if !request.Xray.Active || request.Xray.PID != 777 {
		t.Fatalf("xray status = %+v", request.Xray)
	}
	if request.Xray.UptimeS != 99 {
		t.Errorf("uptime = %d, want 99", request.Xray.UptimeS)
	}
}

func TestHeartbeatCommands(t *testing.T) {
	h := newHarness(t)
	h.deliverConfig(2)
	if err := h.runner.ConfigPollOnce(context.Background()); err != nil {
		t.Fatal(err)
	}
	h.panel.mu.Lock()
	h.panel.revision = 2
	h.panel.heartbeatResp = api.HeartbeatResponse{Commands: []api.Command{
		{ID: "cmd-diagnose", Type: api.CommandDiagnose},
		{ID: "cmd-restart", Type: api.CommandRestartService},
		{ID: "cmd-unknown", Type: "no_such_command"},
	}}
	h.panel.mu.Unlock()
	if err := h.runner.HeartbeatOnce(context.Background()); err != nil {
		t.Fatalf("HeartbeatOnce: %v", err)
	}
	h.panel.mu.Lock()
	diagnose := len(h.panel.diagnose)
	commandResults := append([]api.CommandResult(nil), h.panel.commandResult...)
	h.panel.mu.Unlock()
	if diagnose != 1 {
		t.Errorf("diagnose reports = %d", diagnose)
	}
	statuses := map[string]string{}
	for _, result := range commandResults {
		statuses[result.CommandID] = result.Status
	}
	if statuses["cmd-diagnose"] != api.CommandDone {
		t.Errorf("diagnose command status = %q", statuses["cmd-diagnose"])
	}
	if statuses["cmd-restart"] != api.CommandDone {
		t.Errorf("restart command status = %q", statuses["cmd-restart"])
	}
	if statuses["cmd-unknown"] != api.CommandError {
		t.Errorf("unknown command status = %q", statuses["cmd-unknown"])
	}
	calls, err := os.ReadFile(h.calls)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(calls), "systemctl restart xray.service") {
		t.Errorf("restart_services did not restart xray: %s", calls)
	}
}

func TestUninstallCommand(t *testing.T) {
	h := newHarness(t)
	err := h.runner.HandleCommand(context.Background(), api.Command{ID: "cmd-uninstall", Type: api.CommandUninstall})
	if !errors.Is(err, ErrUninstallComplete) {
		t.Fatalf("error = %v, want ErrUninstallComplete", err)
	}
	if h.uninstallCalls != 1 {
		t.Fatalf("uninstall calls = %d", h.uninstallCalls)
	}
	if h.selfStops != 1 {
		t.Fatalf("self stop calls = %d", h.selfStops)
	}
	h.panel.mu.Lock()
	uninstalls := append([]string(nil), h.panel.uninstalls...)
	results := append([]api.CommandResult(nil), h.panel.commandResult...)
	h.panel.mu.Unlock()
	if len(uninstalls) != 1 || uninstalls[0] != "done" {
		t.Fatalf("uninstall reports = %v", uninstalls)
	}
	if len(results) != 1 || results[0].Status != api.CommandDone {
		t.Fatalf("command results = %+v", results)
	}
}

func TestMetricsDeltasAndReplay(t *testing.T) {
	h := newHarness(t)
	h.writeStats(1000, 2000)
	h.appendAccessLog(
		"2026/11/09 22:13:37.279010 from 203.0.113.7:47017 accepted tcp:example.com:443 [vless-in >> direct] email: client-1@vpn",
		"2026/11/09 22:13:38.279010 from 203.0.113.7:47018 accepted tcp:blocked.example:443 [vless-in >> block] email: client-1@vpn",
	)
	ctx := context.Background()
	if err := h.runner.MetricsOnce(ctx); err != nil {
		t.Fatalf("MetricsOnce: %v", err)
	}
	h.panel.mu.Lock()
	first := append([]api.MetricsBatch(nil), h.panel.metrics...)
	h.panel.mu.Unlock()
	if len(first) != 1 {
		t.Fatalf("posted batches = %d", len(first))
	}
	if first[0].TrafficDelta.Up != 1000 || first[0].TrafficDelta.Down != 2000 {
		t.Fatalf("first deltas = %+v", first[0].TrafficDelta)
	}
	if first[0].Connections.Accepted15s != 2 || first[0].Connections.Blocked15s != 1 {
		t.Fatalf("connections = %+v", first[0].Connections)
	}

	h.writeStats(1500, 2600)
	if err := h.runner.MetricsOnce(ctx); err != nil {
		t.Fatalf("second MetricsOnce: %v", err)
	}
	h.panel.mu.Lock()
	second := h.panel.metrics[len(h.panel.metrics)-1]
	h.panel.mu.Unlock()
	if second.TrafficDelta.Up != 500 || second.TrafficDelta.Down != 600 {
		t.Fatalf("second deltas = %+v", second.TrafficDelta)
	}

	// Panel outage: the batch must be buffered locally.
	h.writeStats(1600, 2700)
	h.panel.mu.Lock()
	h.panel.metricsStatus = http.StatusInternalServerError
	h.panel.mu.Unlock()
	if err := h.runner.MetricsOnce(ctx); err != nil {
		t.Fatalf("offline MetricsOnce: %v", err)
	}
	buffered, err := os.ReadFile(h.paths.MetricsBuffer)
	if err != nil || len(buffered) == 0 {
		t.Fatalf("offline batch was not buffered: %v", err)
	}
	if snap := h.store.Snapshot(); snap.Counters["user>>>client-1@vpn>>>traffic>>>uplink"] != 1600 {
		t.Fatalf("baseline did not advance while offline: %v", snap.Counters)
	}

	// Recovery: the buffered batch is replayed first, then the current one.
	h.panel.mu.Lock()
	h.panel.metricsStatus = 0
	h.panel.mu.Unlock()
	h.appendAccessLog("2026/11/09 22:13:39.180044 from 203.0.113.9:46705 rejected  proxy/vless/encoding: invalid request version")
	h.writeStats(1700, 2800)
	if err := h.runner.MetricsOnce(ctx); err != nil {
		t.Fatalf("recovery MetricsOnce: %v", err)
	}
	h.panel.mu.Lock()
	recovered := append([]api.MetricsBatch(nil), h.panel.metrics...)
	h.panel.mu.Unlock()
	replayIndex := -1
	for i := len(recovered) - 1; i >= 0; i-- {
		if recovered[i].Replay {
			replayIndex = i
			break
		}
	}
	if replayIndex < 0 {
		t.Fatalf("no replayed batch was delivered: %+v", recovered)
	}
	if recovered[replayIndex].TrafficDelta.Up != 100 {
		t.Errorf("replayed delta = %+v", recovered[replayIndex].TrafficDelta)
	}
	last := recovered[len(recovered)-1]
	if last.Replay || last.TrafficDelta.Up != 100 {
		t.Errorf("current batch after recovery = %+v", last)
	}
	if _, err := os.Stat(h.paths.MetricsBuffer); !os.IsNotExist(err) {
		t.Errorf("buffer was not drained")
	}
}

func TestUnauthorizedKeepsWorking(t *testing.T) {
	h := newHarness(t)
	h.panel.mu.Lock()
	h.panel.heartbeatStatus = http.StatusUnauthorized
	h.panel.mu.Unlock()
	if err := h.runner.HeartbeatOnce(context.Background()); err != nil {
		t.Fatalf("HeartbeatOnce must swallow 401: %v", err)
	}
	h.panel.mu.Lock()
	firstCalls := h.panel.heartbeatCalls
	h.panel.mu.Unlock()
	if !h.runner.authBlocked() {
		t.Fatalf("agent did not enter the auth backoff")
	}
	// During the backoff window no panel request must be made.
	if err := h.runner.HeartbeatOnce(context.Background()); err != nil {
		t.Fatalf("second HeartbeatOnce: %v", err)
	}
	h.panel.mu.Lock()
	secondCalls := h.panel.heartbeatCalls
	h.panel.mu.Unlock()
	if secondCalls != firstCalls {
		t.Fatalf("panel was contacted during the auth backoff: %d -> %d", firstCalls, secondCalls)
	}
}

func TestResumePendingApply(t *testing.T) {
	h := newHarness(t)
	if err := h.store.Update(func(s *state.State) error {
		s.PendingApply = &state.PendingApply{Revision: 5, Status: state.ApplyApplied, SHA256: "abc", UpdatedAt: time.Now()}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := h.runner.Prepare(context.Background()); err != nil {
		t.Fatalf("Prepare: %v", err)
	}
	h.panel.mu.Lock()
	results := append([]api.ApplyResult(nil), h.panel.applyResults...)
	h.panel.mu.Unlock()
	if len(results) != 1 || results[0].Revision != 5 || results[0].Status != api.StatusApplied {
		t.Fatalf("apply results = %+v", results)
	}
	if h.store.Snapshot().PendingApply != nil {
		t.Fatalf("pending apply was not cleared")
	}
}

func TestResumeInProgressApplyReportsFailure(t *testing.T) {
	h := newHarness(t)
	if err := h.store.Update(func(s *state.State) error {
		s.PendingApply = &state.PendingApply{Revision: 6, Status: state.ApplyInProgress, UpdatedAt: time.Now()}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := h.runner.Prepare(context.Background()); err != nil {
		t.Fatalf("Prepare: %v", err)
	}
	h.panel.mu.Lock()
	results := append([]api.ApplyResult(nil), h.panel.applyResults...)
	h.panel.mu.Unlock()
	if len(results) != 1 || results[0].Status != api.StatusFailed {
		t.Fatalf("apply results = %+v", results)
	}
	if !strings.Contains(results[0].Error, "restarted while applying") {
		t.Errorf("error = %q", results[0].Error)
	}
}

func TestUpdateAgentCommand(t *testing.T) {
	h := newHarness(t)
	// An existing agent binary is replaced by the downloaded artifact.
	if err := os.MkdirAll(h.paths.BinDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(h.paths.AgentBin, []byte("old-binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	artifact := []byte("new-agent-binary")
	h.panel.mu.Lock()
	h.panel.artifacts["vpn-agent-linux-amd64"] = artifact
	h.panel.mu.Unlock()
	if err := h.runner.HandleCommand(context.Background(), api.Command{ID: "cmd-update", Type: api.CommandUpdateAgent}); err != nil {
		t.Fatalf("HandleCommand: %v", err)
	}
	installed, err := os.ReadFile(h.paths.AgentBin)
	if err != nil {
		t.Fatal(err)
	}
	if string(installed) != string(artifact) {
		t.Fatalf("installed binary = %q", installed)
	}
	backup, err := os.ReadFile(h.paths.AgentBin + ".old")
	if err != nil || string(backup) != "old-binary" {
		t.Fatalf("backup binary = %q, %v", backup, err)
	}
	if h.selfRestarts != 1 {
		t.Fatalf("self restarts = %d", h.selfRestarts)
	}
	h.panel.mu.Lock()
	results := append([]api.CommandResult(nil), h.panel.commandResult...)
	h.panel.mu.Unlock()
	if len(results) != 1 || results[0].Status != api.CommandDone {
		t.Fatalf("command results = %+v", results)
	}
}

func TestRunLoop(t *testing.T) {
	h := newHarness(t)
	h.deliverConfig(1)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- h.runner.Run(ctx) }()
	deadline := time.After(3 * time.Second)
	for {
		select {
		case err := <-done:
			t.Fatalf("run loop exited early: %v", err)
		case <-deadline:
			t.Fatalf("run loop did not apply the revision in time")
		case <-time.After(20 * time.Millisecond):
		}
		h.panel.mu.Lock()
		applied := len(h.panel.applyResults)
		h.panel.mu.Unlock()
		if applied > 0 {
			break
		}
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatalf("run loop did not stop after cancel")
	}
}
