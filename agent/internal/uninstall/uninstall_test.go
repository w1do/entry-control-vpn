package uninstall

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
)

// writeExecutable writes a fake command.
func writeExecutable(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
}

func TestUninstallRun(t *testing.T) {
	home := t.TempDir()
	base := filepath.Join(home, "config", "vpn")
	paths := config.New(base).Paths()
	for _, dir := range []string{paths.BinDir, paths.EtcDir, paths.LogDir, paths.StateDir} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(paths.AgentConfig, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	xrayConfig := `{"inbounds":[{"port":443},{"port":18443}]}`
	if err := os.WriteFile(paths.XrayConfig, []byte(xrayConfig), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(paths.XrayBin, []byte("binary"), 0o755); err != nil {
		t.Fatal(err)
	}

	toolsDir := filepath.Join(home, "tools")
	if err := os.MkdirAll(toolsDir, 0o755); err != nil {
		t.Fatal(err)
	}
	calls := filepath.Join(toolsDir, "calls.log")
	writeExecutable(t, filepath.Join(toolsDir, "systemctl"), "echo \"systemctl $*\" >> "+calls+"\nexit 0")
	writeExecutable(t, filepath.Join(toolsDir, "nft"), "echo \"nft $*\" >> "+calls+"\nif [ \"$1\" = \"list\" ]; then echo 'table inet vpn_agent'; fi\nexit 0")
	writeExecutable(t, filepath.Join(toolsDir, "ufw"), "echo \"ufw $*\" >> "+calls+"\nexit 0")

	unitDir := filepath.Join(home, "units")
	if err := os.MkdirAll(unitDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, unit := range []string{"vpn-agent.service", "xray.service", "unrelated.service"} {
		if err := os.WriteFile(filepath.Join(unitDir, unit), []byte("[Unit]\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	uninstaller := New(Options{
		Paths:        paths,
		Exec:         execx.OSRunner{},
		HomeDir:      home,
		UnitDir:      unitDir,
		AgentUnit:    "vpn-agent.service",
		XrayUnit:     "xray.service",
		SystemctlBin: filepath.Join(toolsDir, "systemctl"),
		NftBin:       filepath.Join(toolsDir, "nft"),
		UfwBin:       filepath.Join(toolsDir, "ufw"),
	})
	report, err := uninstaller.Run(context.Background())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if report.BaseDir != base {
		t.Errorf("base dir = %q, want %q", report.BaseDir, base)
	}
	if len(report.Steps) == 0 {
		t.Errorf("report has no steps")
	}
	if !containsInt(report.FirewallPorts, 443) || !containsInt(report.FirewallPorts, 18443) {
		t.Errorf("firewall ports = %v", report.FirewallPorts)
	}
	if _, err := os.Stat(base); !os.IsNotExist(err) {
		t.Fatalf("base directory was not removed")
	}
	for _, unit := range []string{"vpn-agent.service", "xray.service"} {
		if _, err := os.Stat(filepath.Join(unitDir, unit)); !os.IsNotExist(err) {
			t.Errorf("unit %s was not removed", unit)
		}
	}
	if _, err := os.Stat(filepath.Join(unitDir, "unrelated.service")); err != nil {
		t.Errorf("unrelated unit was removed: %v", err)
	}
	callLog, err := os.ReadFile(calls)
	if err != nil {
		t.Fatalf("calls: %v", err)
	}
	for _, want := range []string{"systemctl stop xray.service", "disable vpn-agent.service", "daemon-reload", "reset-failed", "nft delete table inet vpn_agent", "ufw delete allow 443/tcp"} {
		if !strings.Contains(string(callLog), want) {
			t.Errorf("missing call %q in %s", want, callLog)
		}
	}
}

func TestUninstallRefusesDangerousBase(t *testing.T) {
	home := t.TempDir()
	paths := config.New(filepath.Join(home, "config", "vpn")).Paths()
	uninstaller := New(Options{
		Paths:   paths,
		Exec:    execx.OSRunner{},
		HomeDir: filepath.Join(home, "config", "vpn"),
	})
	if _, err := uninstaller.Run(context.Background()); err == nil {
		t.Fatalf("expected the guard to refuse the home directory as base")
	}
	if _, err := os.Stat(home); err != nil {
		t.Fatalf("home directory was touched: %v", err)
	}
}

func containsInt(values []int, want int) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}
