package systemd

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"vpn-agent/internal/execx"
)

// writeSystemctl writes a fake systemctl script and returns its path.
func writeSystemctl(t *testing.T, body string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "systemctl")
	script := "#!/bin/sh\n" + body + "\n"
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write fake systemctl: %v", err)
	}
	return path
}

func TestParseShow(t *testing.T) {
	output := []byte(`LoadState=loaded
ActiveState=active
SubState=running
MainPID=4321
ActiveEnterTimestampMonotonic=5000000
`)
	status := ParseShow(output)
	if status.LoadState != "loaded" || status.ActiveState != "active" || status.SubState != "running" {
		t.Fatalf("parsed status = %+v", status)
	}
	if status.MainPID != 4321 {
		t.Errorf("pid = %d", status.MainPID)
	}
	if status.ActiveEnterTimestampMonotonic != 5000000 {
		t.Errorf("monotonic = %d", status.ActiveEnterTimestampMonotonic)
	}
}

func TestUptimeSeconds(t *testing.T) {
	status := Status{ActiveEnterTimestampMonotonic: 5_000_000} // 5 seconds
	if got := UptimeSeconds(status, 105.0); got != 100 {
		t.Errorf("uptime = %d, want 100", got)
	}
	if got := UptimeSeconds(status, 0); got != 0 {
		t.Errorf("uptime without boot time = %d", got)
	}
	if got := UptimeSeconds(Status{}, 100); got != 0 {
		t.Errorf("uptime without timestamp = %d", got)
	}
	if got := UptimeSeconds(Status{ActiveEnterTimestampMonotonic: 10_000_000}, 5); got != 0 {
		t.Errorf("negative uptime must be clamped, got %d", got)
	}
}

func TestShowAndIsActiveWithFakeSystemctl(t *testing.T) {
	script := writeSystemctl(t, `case "$1" in
  show)
    echo "LoadState=loaded"
    echo "ActiveState=active"
    echo "SubState=running"
    echo "MainPID=321"
    echo "ActiveEnterTimestampMonotonic=2000000"
    exit 0
    ;;
  is-active)
    echo active
    exit 0
    ;;
  *)
    exit 0
    ;;
esac`)
	status, err := Show(context.Background(), execx.OSRunner{}, script, "xray.service")
	if err != nil {
		t.Fatalf("Show: %v", err)
	}
	if !status.Active() || status.MainPID != 321 {
		t.Fatalf("status = %+v", status)
	}
	active, err := IsActive(context.Background(), execx.OSRunner{}, script, "xray.service")
	if err != nil || !active {
		t.Fatalf("IsActive = %v, %v", active, err)
	}
}

func TestIsActiveInactiveIsNotAnError(t *testing.T) {
	script := writeSystemctl(t, "exit 3")
	active, err := IsActive(context.Background(), execx.OSRunner{}, script, "xray.service")
	if err != nil {
		t.Fatalf("IsActive: %v", err)
	}
	if active {
		t.Fatalf("expected inactive")
	}
}

func TestRestartFailure(t *testing.T) {
	script := writeSystemctl(t, `if [ "$1" = "restart" ]; then echo "Job failed" >&2; exit 1; fi
exit 0`)
	if err := Restart(context.Background(), execx.OSRunner{}, script, "xray.service", time.Second); err == nil {
		t.Fatalf("expected restart error")
	}
}

func TestRestartNoBlockArgs(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "calls.log")
	script := writeSystemctl(t, `echo "$@" >> `+logPath+`
exit 0`)
	if err := RestartNoBlock(context.Background(), execx.OSRunner{}, script, "vpn-agent.service", time.Second); err != nil {
		t.Fatalf("RestartNoBlock: %v", err)
	}
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("read calls log: %v", err)
	}
	if got := string(data); got != "--no-block restart vpn-agent.service\n" {
		t.Fatalf("systemctl args = %q", got)
	}
}
