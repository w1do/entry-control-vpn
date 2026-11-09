package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// writeConfig writes <base>/etc/agent.json.
func writeConfig(t *testing.T, base, body string) {
	t.Helper()
	etc := filepath.Join(base, "etc")
	if err := os.MkdirAll(etc, 0o700); err != nil {
		t.Fatalf("mkdir etc: %v", err)
	}
	if err := os.WriteFile(filepath.Join(etc, "agent.json"), []byte(body), 0o600); err != nil {
		t.Fatalf("write agent.json: %v", err)
	}
}

func TestLoadDefaultsAndPaths(t *testing.T) {
	base := t.TempDir()
	writeConfig(t, base, `{"panel_url":"https://panel.example.com","token":"secret","server_id":7,"base_dir":"`+base+`"}`)
	cfg, err := Load(base)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.HeartbeatIntervalS != DefaultHeartbeatIntervalS {
		t.Errorf("heartbeat interval = %d", cfg.HeartbeatIntervalS)
	}
	if cfg.ConfigPollS != DefaultConfigPollS {
		t.Errorf("config poll = %d", cfg.ConfigPollS)
	}
	if cfg.MetricsIntervalS != DefaultMetricsIntervalS {
		t.Errorf("metrics interval = %d", cfg.MetricsIntervalS)
	}
	if cfg.XrayAPIAddr != "127.0.0.1:10085" {
		t.Errorf("xray api addr = %q", cfg.XrayAPIAddr)
	}
	if cfg.XrayUnit != "xray.service" || cfg.AgentUnit != "vpn-agent.service" {
		t.Errorf("units = %q %q", cfg.XrayUnit, cfg.AgentUnit)
	}
	paths := cfg.Paths()
	wantXray := filepath.Join(base, "bin", "xray")
	if paths.XrayBin != wantXray {
		t.Errorf("xray bin = %q want %q", paths.XrayBin, wantXray)
	}
	if paths.AccessLog != filepath.Join(base, "log", "access.log") {
		t.Errorf("access log = %q", paths.AccessLog)
	}
	if paths.MetricsBuffer != filepath.Join(base, "state", "metrics-buffer.jsonl") {
		t.Errorf("metrics buffer = %q", paths.MetricsBuffer)
	}
	if cfg.AssetDir != filepath.Join(base, "etc") {
		t.Errorf("asset dir = %q, want <base>/etc (panel provisioner layout)", cfg.AssetDir)
	}
	artifact := cfg.EffectiveArtifactName()
	if !strings.Contains(artifact, runtime.GOOS) || !strings.Contains(artifact, runtime.GOARCH) {
		t.Errorf("artifact name = %q", artifact)
	}
	if len(cfg.Warnings) != 0 {
		t.Errorf("unexpected warnings: %v", cfg.Warnings)
	}
}

func TestLoadValidation(t *testing.T) {
	base := t.TempDir()
	cases := map[string]string{
		"missing panel_url": `{"token":"secret"}`,
		"missing token":     `{"panel_url":"https://panel.example.com"}`,
		"bad scheme":        `{"panel_url":"ftp://panel","token":"x"}`,
		"relative dir":      `{"panel_url":"https://panel.example.com","token":"x","base_dir":"relative/path"}`,
		"bad interval":      `{"panel_url":"https://panel.example.com","token":"x","heartbeat_interval_s":1}`,
		"bad xray addr":     `{"panel_url":"https://panel.example.com","token":"x","xray_api_addr":"nope"}`,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			writeConfig(t, base, body)
			if _, err := Load(base); err == nil {
				t.Fatalf("expected an error")
			}
		})
	}
}

func TestTokenPermissionWarning(t *testing.T) {
	base := t.TempDir()
	body := `{"panel_url":"https://panel.example.com","token":"secret","base_dir":"` + base + `"}`
	writeConfig(t, base, body)
	if err := os.Chmod(filepath.Join(base, "etc", "agent.json"), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(base)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if len(cfg.Warnings) == 0 {
		t.Fatalf("expected a token permission warning")
	}
}

func TestEnsureDirs(t *testing.T) {
	base := t.TempDir()
	writeConfig(t, base, `{"panel_url":"https://panel.example.com","token":"secret","base_dir":"`+base+`"}`)
	cfg, err := Load(base)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if err := cfg.EnsureDirs(); err != nil {
		t.Fatalf("EnsureDirs: %v", err)
	}
	for _, dir := range []string{"bin", "etc", "log", "state"} {
		info, err := os.Stat(filepath.Join(base, dir))
		if err != nil {
			t.Fatalf("stat %s: %v", dir, err)
		}
		if !info.IsDir() {
			t.Fatalf("%s is not a directory", dir)
		}
	}
	for _, secret := range []string{"etc", "state"} {
		info, err := os.Stat(filepath.Join(base, secret))
		if err != nil {
			t.Fatal(err)
		}
		if mode := info.Mode().Perm(); mode != 0o700 {
			t.Errorf("%s mode = %04o, want 0700", secret, mode)
		}
	}
}

func TestDefaultBaseDirHelper(t *testing.T) {
	if got := defaultBaseDir("/home/dev", false); got != "/home/dev/config/vpn" {
		t.Errorf("non-root default = %q", got)
	}
	if got := defaultBaseDir("/home/dev", true); got != "/root/config/vpn" {
		t.Errorf("root default = %q", got)
	}
	t.Setenv(EnvBaseDir, "/tmp/agent-base")
	got, err := DefaultBaseDir()
	if err != nil {
		t.Fatalf("DefaultBaseDir: %v", err)
	}
	if got != "/tmp/agent-base" {
		t.Errorf("env default = %q", got)
	}
}

func TestFinalizeRelativeBaseRejected(t *testing.T) {
	cfg := New("relative-path")
	cfg.PanelURL = "https://panel.example.com"
	cfg.Token = "x"
	if err := cfg.Finalize(); err == nil {
		t.Fatalf("expected relative base dir to be rejected")
	}
}
