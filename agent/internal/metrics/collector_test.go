package metrics

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/logs"
	"vpn-agent/internal/state"
)

const (
	collectorAccepted = "2026/11/09 22:13:37.279010 from 203.0.113.7:47017 accepted tcp:example.com:443 [vless-in >> direct] email: a@b\n"
	collectorBlocked  = "2026/11/09 22:13:38.279010 from 203.0.113.7:47018 accepted tcp:blocked.example:443 [vless-in >> block] email: a@b\n"
	collectorRejected = "2026/11/09 22:13:39.180044 from 203.0.113.9:46705 rejected  proxy/vless/encoding: invalid request version\n"
)

// setupCollector builds a collector over fake xray and systemctl scripts.
func setupCollector(t *testing.T, statsJSON, onlineJSON string, withSystemd bool) (*Collector, string) {
	t.Helper()
	dir := t.TempDir()
	base := filepath.Join(dir, "config", "vpn")
	paths := config.New(base).Paths()
	if err := os.MkdirAll(paths.EtcDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(paths.LogDir, 0o700); err != nil {
		t.Fatal(err)
	}
	writeFile(t, paths.AccessLog, collectorAccepted+collectorBlocked+collectorRejected)
	writeFile(t, paths.ErrorLog, "2026/11/09 22:00:00 [Info] started\n2026/11/09 22:00:01 [Error] boom\n")

	binary := writeFakeXray(t, dir, statsJSON, `{"NumGoroutine":5,"Uptime":2}`, onlineJSON, "{}", `{"users":[{"email":"a@b"}]}`)

	systemctl := ""
	if withSystemd {
		systemctl = filepath.Join(dir, "systemctl")
		script := "#!/bin/sh\n" +
			"if [ \"$1\" = \"show\" ]; then\n" +
			"  echo \"LoadState=loaded\"\n" +
			"  echo \"ActiveState=active\"\n" +
			"  echo \"SubState=running\"\n" +
			"  echo \"MainPID=4242\"\n" +
			"  echo \"ActiveEnterTimestampMonotonic=5000000\"\n" +
			"  exit 0\n" +
			"fi\n" +
			"exit 0\n"
		if err := os.WriteFile(systemctl, []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
	}

	probe := NewXrayProbe(execx.OSRunner{}, binary, "127.0.0.1:10085", "", nil)
	collector := NewCollector(Options{
		Paths:              paths,
		Probe:              probe,
		Runner:             execx.OSRunner{},
		SystemctlBin:       systemctl,
		XrayUnit:           "xray.service",
		AgentUnit:          "vpn-agent.service",
		Logs:               logs.Collector{AccessPath: paths.AccessLog, ErrorPath: paths.ErrorLog},
		OnlineWindow:       2 * time.Minute,
		AgentVersion:       "test-version",
		IncludeDiagnostics: true,
		ProcUptime:         func() (float64, error) { return 100, nil },
		Now:                func() time.Time { return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC) },
	})
	return collector, binary
}

func TestCollectorCollect(t *testing.T) {
	stats := `{"stat":[
		{"name":"user>>>a@b>>>traffic>>>uplink","value":1000},
		{"name":"user>>>a@b>>>traffic>>>downlink","value":2000},
		{"name":"inbound>>>vless-in>>>traffic>>>uplink","value":50},
		{"name":"inbound>>>vless-in>>>traffic>>>downlink","value":60},
		{"name":"outbound>>>direct>>>traffic>>>uplink","value":70},
		{"name":"outbound>>>direct>>>traffic>>>downlink","value":80}
	]}`
	collector, _ := setupCollector(t, stats, `{"users":[{"email":"a@b","ips":["203.0.113.7"]}]}`, true)
	ctx := context.Background()
	snapshot, err := collector.Collect(ctx, Previous{LogOffsets: state.LogOffsets{}}, 5)
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	batch := snapshot.Batch
	if batch.Revision != 5 || batch.AgentVersion != "test-version" {
		t.Errorf("batch revision/version = %d %q", batch.Revision, batch.AgentVersion)
	}
	if !batch.Xray.Active || batch.Xray.UptimeS != 95 || batch.Xray.PID != 4242 || batch.Xray.Version != "26.11.9" {
		t.Errorf("xray status = %+v", batch.Xray)
	}
	if batch.TrafficDelta.Up != 1000 || batch.TrafficDelta.Down != 2000 {
		t.Errorf("traffic delta = %+v", batch.TrafficDelta)
	}
	if batch.Connections.Accepted15s != 2 || batch.Connections.Blocked15s != 1 || batch.Connections.Online != 1 {
		t.Errorf("connections = %+v", batch.Connections)
	}
	if batch.Errors.Rejected15s != 1 || batch.Errors.XrayError15s != 1 {
		t.Errorf("errors = %+v", batch.Errors)
	}
	if len(batch.Clients) != 1 {
		t.Fatalf("clients = %+v", batch.Clients)
	}
	client := batch.Clients[0]
	if client.Email != "a@b" || client.Up != 1000 || client.Down != 2000 || !client.Online {
		t.Errorf("client = %+v", client)
	}
	if len(client.IPs) != 1 || client.IPs[0] != "203.0.113.7" {
		t.Errorf("client ips = %v", client.IPs)
	}
	if batch.Diagnostics == nil || batch.Diagnostics.Inbound["vless-in"].Up != 50 || batch.Diagnostics.Outbound["direct"].Down != 80 {
		t.Errorf("diagnostics = %+v", batch.Diagnostics)
	}
	if snapshot.Baseline["user>>>a@b>>>traffic>>>uplink"] != 1000 {
		t.Errorf("baseline = %v", snapshot.Baseline)
	}
	if snapshot.LogOffsets.Access == 0 || snapshot.LogOffsets.Error == 0 {
		t.Errorf("log offsets = %+v", snapshot.LogOffsets)
	}
	if len(snapshot.Degraded) != 0 {
		t.Errorf("degraded = %v", snapshot.Degraded)
	}
}

func TestCollectorComputesDeltasAcrossIntervals(t *testing.T) {
	dir := t.TempDir()
	binary := writeFakeXray(t, dir,
		`{"stat":[{"name":"user>>>a@b>>>traffic>>>uplink","value":1000}]}`,
		"{}", "{}", "{}", "{}")
	probe := NewXrayProbe(execx.OSRunner{}, binary, "127.0.0.1:10085", "", nil)
	paths := config.New(filepath.Join(dir, "config", "vpn")).Paths()
	collector := NewCollector(Options{
		Paths:        paths,
		Probe:        probe,
		Runner:       execx.OSRunner{},
		SystemctlBin: filepath.Join(dir, "systemctl"),
		ProcUptime:   func() (float64, error) { return 0, nil },
	})
	first, err := collector.Collect(context.Background(), Previous{}, 1)
	if err != nil {
		t.Fatal(err)
	}
	if first.Batch.TrafficDelta.Up != 1000 {
		t.Fatalf("first delta = %+v", first.Batch.TrafficDelta)
	}
	// Update the fake stats.
	writeFile(t, filepath.Join(dir, "stats.json"), `{"stat":[{"name":"user>>>a@b>>>traffic>>>uplink","value":1100}]}`)
	second, err := collector.Collect(context.Background(), Previous{Counters: first.Baseline, LogOffsets: first.LogOffsets}, 2)
	if err != nil {
		t.Fatal(err)
	}
	if second.Batch.TrafficDelta.Up != 100 {
		t.Fatalf("second delta = %+v", second.Batch.TrafficDelta)
	}
	// Counter reset must not produce a negative delta and must not lose data.
	writeFile(t, filepath.Join(dir, "stats.json"), `{"stat":[{"name":"user>>>a@b>>>traffic>>>uplink","value":7}]}`)
	third, err := collector.Collect(context.Background(), Previous{Counters: second.Baseline, LogOffsets: second.LogOffsets}, 3)
	if err != nil {
		t.Fatal(err)
	}
	if third.Batch.TrafficDelta.Up != 7 {
		t.Fatalf("reset delta = %+v", third.Batch.TrafficDelta)
	}
}

func TestCollectorOnlineFallback(t *testing.T) {
	collector, _ := setupCollector(t, "{}", "{}", true)
	// Make sure the fake xray reports no online users: the access log email
	// must still mark the client online through last_seen.
	snapshot, err := collector.Collect(context.Background(), Previous{}, 1)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Batch.Connections.Online < 1 {
		t.Fatalf("online fallback = %+v", snapshot.Batch.Connections)
	}
	found := false
	for _, client := range snapshot.Batch.Clients {
		if client.Email == "a@b" && client.Online {
			found = true
		}
	}
	if !found {
		t.Fatalf("client from the access log was not reported online: %+v", snapshot.Batch.Clients)
	}
	if _, ok := snapshot.LastSeen["a@b"]; !ok {
		t.Fatalf("last seen was not recorded: %v", snapshot.LastSeen)
	}
}

func TestCollectorDegradedWhenSystemdAndStatsFail(t *testing.T) {
	dir := t.TempDir()
	binary := filepath.Join(dir, "xray")
	brokenXray := "#!/bin/sh\n" +
		"if [ \"$1\" = \"version\" ]; then echo \"Xray 26.11.9\"; exit 0; fi\n" +
		"exit 1\n"
	if err := os.WriteFile(binary, []byte(brokenXray), 0o755); err != nil {
		t.Fatal(err)
	}
	probe := NewXrayProbe(execx.OSRunner{}, binary, "127.0.0.1:10085", "", nil)
	brokenSystemctl := filepath.Join(dir, "systemctl")
	if err := os.WriteFile(brokenSystemctl, []byte("#!/bin/sh\nexit 1\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	paths := config.New(filepath.Join(dir, "config", "vpn")).Paths()
	collector := NewCollector(Options{
		Paths:        paths,
		Probe:        probe,
		Runner:       execx.OSRunner{},
		SystemctlBin: brokenSystemctl,
		ProcUptime:   func() (float64, error) { return 0, nil },
	})
	snapshot, err := collector.Collect(context.Background(), Previous{Counters: map[string]int64{"keep": 3}}, 1)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Batch.Xray.Active {
		t.Fatalf("xray must be reported inactive when systemctl fails")
	}
	if len(snapshot.Degraded) == 0 {
		t.Fatalf("expected degraded reasons")
	}
	if snapshot.Baseline["keep"] != 3 {
		t.Fatalf("baseline must be preserved when stats are unavailable: %v", snapshot.Baseline)
	}
	if !strings.Contains(strings.Join(snapshot.Degraded, ";"), "systemctl") {
		t.Errorf("degraded = %v", snapshot.Degraded)
	}
}
