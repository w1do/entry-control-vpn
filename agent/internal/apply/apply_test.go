package apply

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/metrics"
)

// testEnv is a fake node: fake xray, fake systemctl and a temp base dir.
type testEnv struct {
	dir       string
	base      string
	paths     config.Paths
	xray      string
	systemctl string
	calls     string
}

// setupEnv writes the fake tool scripts and returns the environment.
func setupEnv(t *testing.T) *testEnv {
	t.Helper()
	dir := t.TempDir()
	base := filepath.Join(dir, "config", "vpn")
	paths := config.New(base).Paths()
	if err := os.MkdirAll(paths.EtcDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(paths.StateDir, 0o700); err != nil {
		t.Fatal(err)
	}
	env := &testEnv{dir: dir, base: base, paths: paths}
	env.calls = filepath.Join(dir, "calls.log")

	xrayScript := "#!/bin/sh\n" +
		"echo \"xray $*\" >> " + env.calls + "\n" +
		"if [ \"$1\" = \"run\" ]; then\n" +
		"  code=0\n" +
		"  if [ -f " + filepath.Join(dir, "test-exit") + " ]; then code=$(cat " + filepath.Join(dir, "test-exit") + "); fi\n" +
		"  if [ \"$code\" != \"0\" ]; then\n" +
		"    echo \"Failed to start: main: failed to load config files: invalid character\"\n" +
		"    exit $code\n" +
		"  fi\n" +
		"  echo \"Configuration OK.\"\n" +
		"  exit 0\n" +
		"fi\n" +
		"if [ \"$1\" = \"version\" ]; then echo \"Xray 26.11.9 (Xray, Penetrates Everything.)\"; exit 0; fi\n" +
		"if [ \"$1\" = \"api\" ]; then\n" +
		"  if [ \"$2\" = \"inbounduser\" ]; then cat " + filepath.Join(dir, "inbound.json") + " 2>/dev/null || echo '{}'; exit 0; fi\n" +
		"  echo '{}'\n" +
		"  exit 0\n" +
		"fi\n" +
		"exit 1\n"
	env.xray = filepath.Join(dir, "xray")
	if err := os.WriteFile(env.xray, []byte(xrayScript), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "inbound.json"), []byte(`{"users":[{"email":"client-1@vpn"}]}`), 0o600); err != nil {
		t.Fatal(err)
	}

	systemctlScript := "#!/bin/sh\n" +
		"echo \"systemctl $*\" >> " + env.calls + "\n" +
		"if [ \"$1\" = \"show\" ]; then\n" +
		"  echo \"LoadState=loaded\"\n" +
		"  echo \"ActiveState=active\"\n" +
		"  echo \"SubState=running\"\n" +
		"  echo \"MainPID=777\"\n" +
		"  echo \"ActiveEnterTimestampMonotonic=1000000\"\n" +
		"  exit 0\n" +
		"fi\n" +
		"if [ \"$1\" = \"is-active\" ]; then echo active; exit 0; fi\n" +
		"if [ \"$1\" = \"restart\" ] || [ \"$2\" = \"restart\" ]; then\n" +
		"  if [ -f " + filepath.Join(dir, "restart-exit") + " ]; then exit $(cat " + filepath.Join(dir, "restart-exit") + "); fi\n" +
		"  exit 0\n" +
		"fi\n" +
		"exit 0\n"
	env.systemctl = filepath.Join(dir, "systemctl")
	if err := os.WriteFile(env.systemctl, []byte(systemctlScript), 0o755); err != nil {
		t.Fatal(err)
	}
	return env
}

// applier builds an Applier for the fake environment.
func (e *testEnv) applier() *Applier {
	probe := metrics.NewXrayProbe(execx.OSRunner{}, e.xray, "127.0.0.1:10085", filepath.Join(e.base, "share"), nil)
	return New(Options{
		Paths:              e.paths,
		Exec:               execx.OSRunner{},
		XrayBin:            e.xray,
		SystemctlBin:       e.systemctl,
		XrayUnit:           "xray.service",
		XrayAPIAddr:        "127.0.0.1:10085",
		AssetDir:           filepath.Join(e.base, "share"),
		Probe:              probe,
		HealthTimeout:      400 * time.Millisecond,
		HealthPollInterval: 20 * time.Millisecond,
		TestTimeout:        5 * time.Second,
		RestartTimeout:     5 * time.Second,
	})
}

// candidateConfig returns a valid Xray config listening on port.
func candidateConfig(port int) []byte {
	return []byte(fmt.Sprintf(`{"log":{"loglevel":"warning"},"inbounds":[{"tag":"vless-in","listen":"127.0.0.1","port":%d,"protocol":"vless","settings":{"decryption":"none","clients":[{"id":"11111111-1111-1111-1111-111111111111","email":"client-1@vpn"}]}}],"outbounds":[{"tag":"direct","protocol":"freedom"}]}`, port))
}

// listen starts a TCP listener and returns its port.
func listen(t *testing.T) (net.Listener, int) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	return listener, listener.Addr().(*net.TCPAddr).Port
}

// readCalls returns the recorded fake calls.
func (e *testEnv) readCalls(t *testing.T) string {
	t.Helper()
	data, err := os.ReadFile(e.calls)
	if err != nil {
		if os.IsNotExist(err) {
			return ""
		}
		t.Fatal(err)
	}
	return string(data)
}

func TestApplySuccess(t *testing.T) {
	env := setupEnv(t)
	listener, port := listen(t)
	defer listener.Close()
	previous := []byte(`{"inbounds":[],"outbounds":[]}`)
	if err := os.WriteFile(env.paths.XrayConfig, previous, 0o600); err != nil {
		t.Fatal(err)
	}
	raw := candidateConfig(port)
	result := env.applier().Apply(context.Background(), 7, raw, sha256Hex(raw))
	if result.Status != "applied" {
		t.Fatalf("status = %q error = %q log = %s", result.Status, result.Error, result.LogTail)
	}
	if result.SHA256 != sha256Hex(raw) {
		t.Errorf("sha256 = %q", result.SHA256)
	}
	if !result.Backup {
		t.Errorf("expected a backup of the previous config")
	}
	installed, err := os.ReadFile(env.paths.XrayConfig)
	if err != nil {
		t.Fatal(err)
	}
	if string(installed) != string(raw) {
		t.Fatalf("installed config differs from the candidate")
	}
	info, err := os.Stat(env.paths.XrayConfig)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("config mode = %04o, want 0600", mode)
	}
	backup, err := os.ReadFile(env.paths.XrayBackup)
	if err != nil {
		t.Fatalf("backup: %v", err)
	}
	if string(backup) != string(previous) {
		t.Fatalf("backup content = %q", backup)
	}
	if _, err := os.Stat(env.paths.XrayConfigNew); !os.IsNotExist(err) {
		t.Fatalf("candidate file was not removed")
	}
	calls := env.readCalls(t)
	if !strings.Contains(calls, "xray run -test -c") {
		t.Errorf("xray -test was not called: %s", calls)
	}
	if !strings.Contains(calls, "systemctl restart xray.service") {
		t.Errorf("systemctl restart was not called: %s", calls)
	}
	applyLog, err := os.ReadFile(env.paths.ApplyLog)
	if err != nil {
		t.Fatalf("apply log: %v", err)
	}
	if !strings.Contains(string(applyLog), "health: ok") || !strings.Contains(string(applyLog), "revision=7") {
		t.Errorf("apply log = %s", applyLog)
	}
}

func TestApplyTestFailureKeepsConfig(t *testing.T) {
	env := setupEnv(t)
	previous := []byte(`{"inbounds":[],"outbounds":[]}`)
	if err := os.WriteFile(env.paths.XrayConfig, previous, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(env.dir, "test-exit"), []byte("23"), 0o600); err != nil {
		t.Fatal(err)
	}
	raw := candidateConfig(443)
	result := env.applier().Apply(context.Background(), 8, raw, "")
	if result.Status != "failed" {
		t.Fatalf("status = %q", result.Status)
	}
	if !strings.Contains(result.Error, "xray -test exited with 23") {
		t.Errorf("error = %q", result.Error)
	}
	if !strings.Contains(result.LogTail, "Failed to start") {
		t.Errorf("log tail = %q", result.LogTail)
	}
	installed, err := os.ReadFile(env.paths.XrayConfig)
	if err != nil {
		t.Fatal(err)
	}
	if string(installed) != string(previous) {
		t.Fatalf("config changed on a failed validation")
	}
	if _, err := os.Stat(env.paths.XrayBackup); !os.IsNotExist(err) {
		t.Errorf("a backup must not be created before validation succeeds")
	}
	if _, err := os.Stat(env.paths.XrayConfigNew); !os.IsNotExist(err) {
		t.Errorf("candidate file was not removed")
	}
	if strings.Contains(env.readCalls(t), "systemctl restart") {
		t.Errorf("xray must not be restarted after a failed test")
	}
}

func TestApplyRestartFailureRollsBack(t *testing.T) {
	env := setupEnv(t)
	previous := []byte(`{"inbounds":[],"outbounds":[]}`)
	if err := os.WriteFile(env.paths.XrayConfig, previous, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(env.dir, "restart-exit"), []byte("1"), 0o600); err != nil {
		t.Fatal(err)
	}
	raw := candidateConfig(443)
	result := env.applier().Apply(context.Background(), 9, raw, "")
	if result.Status != "failed" {
		t.Fatalf("status = %q", result.Status)
	}
	if !strings.Contains(result.Error, "systemctl restart") {
		t.Errorf("error = %q", result.Error)
	}
	installed, err := os.ReadFile(env.paths.XrayConfig)
	if err != nil {
		t.Fatal(err)
	}
	if string(installed) != string(previous) {
		t.Fatalf("rollback did not restore the previous config: %q", installed)
	}
}

func TestApplyHealthFailureRollsBack(t *testing.T) {
	env := setupEnv(t)
	previous := []byte(`{"inbounds":[],"outbounds":[]}`)
	if err := os.WriteFile(env.paths.XrayConfig, previous, 0o600); err != nil {
		t.Fatal(err)
	}
	// Nothing listens on the port declared by the candidate config, so the
	// health check fails even though systemctl reports the unit active.
	raw := candidateConfig(49999)
	result := env.applier().Apply(context.Background(), 10, raw, "")
	if result.Status != "failed" {
		t.Fatalf("status = %q", result.Status)
	}
	if !strings.Contains(result.Error, "health check") {
		t.Errorf("error = %q", result.Error)
	}
	installed, err := os.ReadFile(env.paths.XrayConfig)
	if err != nil {
		t.Fatal(err)
	}
	if string(installed) != string(previous) {
		t.Fatalf("rollback did not restore the previous config")
	}
	calls := env.readCalls(t)
	if strings.Count(calls, "systemctl restart xray.service") < 2 {
		t.Errorf("expected the rollback restart: %s", calls)
	}
}

func TestApplySHAMismatchIsWarning(t *testing.T) {
	env := setupEnv(t)
	listener, port := listen(t)
	defer listener.Close()
	raw := candidateConfig(port)
	result := env.applier().Apply(context.Background(), 11, raw, strings.Repeat("0", 64))
	if result.Status != "applied" {
		t.Fatalf("result = %+v", result)
	}
	if !strings.Contains(result.LogTail, "warning: sha256 mismatch") {
		t.Errorf("expected a sha256 warning in the log tail: %q", result.LogTail)
	}
	if _, err := os.Stat(env.paths.XrayConfig); err != nil {
		t.Fatalf("config was not installed: %v", err)
	}
}

func TestApplyCanonicalSHA(t *testing.T) {
	env := setupEnv(t)
	listener, port := listen(t)
	defer listener.Close()
	// Pretty-printed JSON with unsorted keys: the panel hashes the canonical
	// (sorted, compact) form, so the raw bytes hash differs.
	raw := []byte(fmt.Sprintf("{\n  \"outbounds\": [{\"protocol\": \"freedom\", \"tag\": \"direct\"}],\n  \"inbounds\": [{\n    \"settings\": {\"clients\": [{\"email\": \"client-1@vpn\", \"id\": \"11111111-1111-1111-1111-111111111111\"}], \"decryption\": \"none\"},\n    \"protocol\": \"vless\",\n    \"port\": %d,\n    \"listen\": \"127.0.0.1\",\n    \"tag\": \"vless-in\"\n  }]\n}\n", port))
	canonical, ok := canonicalSHA256(raw)
	if !ok {
		t.Fatalf("canonicalSHA256 failed")
	}
	if strings.EqualFold(canonical, sha256Hex(raw)) {
		t.Fatalf("test fixture must not be canonical")
	}
	result := env.applier().Apply(context.Background(), 12, raw, canonical)
	if result.Status != "applied" {
		t.Fatalf("result = %+v", result)
	}
	if !strings.Contains(result.LogTail, "verified (canonical form)") {
		t.Errorf("expected canonical verification log, got %q", result.LogTail)
	}
	if strings.Contains(result.LogTail, "warning: sha256 mismatch") {
		t.Errorf("canonical checksum must not produce a mismatch warning")
	}
}

func TestApplyFirstInstallSuccess(t *testing.T) {
	env := setupEnv(t)
	listener, port := listen(t)
	defer listener.Close()
	raw := candidateConfig(port)
	result := env.applier().Apply(context.Background(), 1, raw, sha256Hex(raw))
	if result.Status != "applied" {
		t.Fatalf("status = %q error = %q", result.Status, result.Error)
	}
	if result.Backup {
		t.Errorf("first install must not report a backup")
	}
	if _, err := os.Stat(env.paths.XrayBackup); !os.IsNotExist(err) {
		t.Errorf("first install created a backup file")
	}
}

func TestApplyClientsMismatchDegraded(t *testing.T) {
	env := setupEnv(t)
	listener, port := listen(t)
	defer listener.Close()
	if err := os.WriteFile(filepath.Join(env.dir, "inbound.json"), []byte(`{"users":[{"email":"someone-else@vpn"}]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	result := env.applier().Apply(context.Background(), 2, candidateConfig(port), "")
	if result.Status != "applied" {
		t.Fatalf("status = %q error = %q", result.Status, result.Error)
	}
	if !strings.Contains(result.DegradedReason, "mismatch") {
		t.Fatalf("degraded reason = %q", result.DegradedReason)
	}
}

func TestApplyInvalidJSON(t *testing.T) {
	env := setupEnv(t)
	result := env.applier().Apply(context.Background(), 3, []byte("{not json"), "")
	if result.Status != "failed" || !strings.Contains(result.Error, "not valid JSON") {
		t.Fatalf("result = %+v", result)
	}
}

func TestApplyFirstInstallHealthFailureRemovesConfig(t *testing.T) {
	env := setupEnv(t)
	raw := candidateConfig(49997)
	result := env.applier().Apply(context.Background(), 4, raw, "")
	if result.Status != "failed" {
		t.Fatalf("status = %q", result.Status)
	}
	if _, err := os.Stat(env.paths.XrayConfig); !os.IsNotExist(err) {
		t.Fatalf("failed first-install config was left in place")
	}
}
