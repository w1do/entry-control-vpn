package metrics

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"vpn-agent/internal/execx"
)

// writeFile is a test helper.
func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

// writeFakeXray writes a fake xray script returning the given responses.
func writeFakeXray(t *testing.T, dir, stats, sysStats, online, allOnline, inbound string) string {
	t.Helper()
	writeFile(t, filepath.Join(dir, "stats.json"), stats)
	writeFile(t, filepath.Join(dir, "sys.json"), sysStats)
	writeFile(t, filepath.Join(dir, "online.json"), online)
	writeFile(t, filepath.Join(dir, "allonline.json"), allOnline)
	writeFile(t, filepath.Join(dir, "inbound.json"), inbound)
	script := "#!/bin/sh\n" +
		"if [ \"$1\" = \"version\" ]; then\n" +
		"  echo \"Xray 26.11.9 (Xray, Penetrates Everything.) 676eaf6 (go1.27.3 linux/amd64)\"\n" +
		"  exit 0\n" +
		"fi\n" +
		"if [ \"$1\" = \"api\" ]; then\n" +
		"  case \"$2\" in\n" +
		"    statsquery) cat " + filepath.Join(dir, "stats.json") + " ;;\n" +
		"    statssys) cat " + filepath.Join(dir, "sys.json") + " ;;\n" +
		"    statsonlineiplist) cat " + filepath.Join(dir, "online.json") + " ;;\n" +
		"    statsgetallonlineusers) cat " + filepath.Join(dir, "allonline.json") + " ;;\n" +
		"    inbounduser) cat " + filepath.Join(dir, "inbound.json") + " ;;\n" +
		"  esac\n" +
		"  exit 0\n" +
		"fi\n" +
		"exit 1\n"
	path := filepath.Join(dir, "xray")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestXrayProbe(t *testing.T) {
	dir := t.TempDir()
	binary := writeFakeXray(t, dir,
		`{"stat":[{"name":"user>>>a@b>>>traffic>>>uplink","value":42}]}`,
		`{"NumGoroutine":12,"Uptime":3}`,
		`{"users":[{"email":"a@b","ips":["203.0.113.7"]}]}`,
		`["a@b"]`,
		`{"users":[{"email":"a@b"}]}`,
	)
	probe := NewXrayProbe(execx.OSRunner{}, binary, "127.0.0.1:10085", dir, nil)
	ctx := context.Background()

	if version := probe.Version(ctx); version != "26.11.9" {
		t.Fatalf("version = %q", version)
	}
	stats, err := probe.StatsQuery(ctx, "user>>>")
	if err != nil {
		t.Fatalf("StatsQuery: %v", err)
	}
	if stats["user>>>a@b>>>traffic>>>uplink"] != 42 {
		t.Fatalf("stats = %v", stats)
	}
	sysStats, err := probe.SysStats(ctx)
	if err != nil || sysStats.Goroutines != 12 || sysStats.UptimeMinutes != 3 {
		t.Fatalf("SysStats = %+v, %v", sysStats, err)
	}
	online, err := probe.OnlineUsers(ctx)
	if err != nil || online.Count() != 1 || !online.Has("a@b") {
		t.Fatalf("OnlineUsers = %+v, %v", online, err)
	}
	emails, err := probe.InboundUserEmails(ctx, "vless-in")
	if err != nil || len(emails) != 1 || emails[0] != "a@b" {
		t.Fatalf("InboundUserEmails = %v, %v", emails, err)
	}
}

func TestXrayProbeOnlineFallback(t *testing.T) {
	dir := t.TempDir()
	binary := writeFakeXray(t, dir,
		"{}",
		"{}",
		"{}",
		`["fallback@example.com"]`,
		"{}",
	)
	probe := NewXrayProbe(execx.OSRunner{}, binary, "127.0.0.1:10085", "", nil)
	online, err := probe.OnlineUsers(context.Background())
	if err != nil {
		t.Fatalf("OnlineUsers: %v", err)
	}
	if !online.Has("fallback@example.com") {
		t.Fatalf("fallback online users = %+v", online)
	}
}

func TestXrayProbeUnsupportedCommands(t *testing.T) {
	dir := t.TempDir()
	script := "#!/bin/sh\nexit 1\n"
	path := filepath.Join(dir, "xray")
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	probe := NewXrayProbe(execx.OSRunner{}, path, "127.0.0.1:10085", "", nil)
	online, err := probe.OnlineUsers(context.Background())
	if err != nil || online.Count() != 0 {
		t.Fatalf("OnlineUsers = %+v, %v", online, err)
	}
	if _, err := probe.InboundUserEmails(context.Background(), "vless-in"); err == nil {
		t.Fatalf("expected an error for an unsupported inbounduser command")
	}
	if version := probe.Version(context.Background()); version != "" {
		t.Fatalf("version = %q", version)
	}
}
