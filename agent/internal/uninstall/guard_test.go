package uninstall

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestGuardBaseWithHome(t *testing.T) {
	home := t.TempDir()
	base := filepath.Join(home, "config", "vpn")
	if err := os.MkdirAll(filepath.Join(base, "etc"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(base, "etc", "agent.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	resolved, err := GuardBaseWithHome(base, home)
	if err != nil {
		t.Fatalf("GuardBaseWithHome: %v", err)
	}
	if resolved != filepath.Clean(base) {
		t.Errorf("resolved = %q, want %q", resolved, base)
	}
}

func TestGuardRefusesDangerousPaths(t *testing.T) {
	home := t.TempDir()
	cases := []struct {
		name   string
		base   string
		reason string
	}{
		{"root", "/", "filesystem root"},
		{"home", home, "home directory"},
		{"etc", "/etc", "protected"},
		{"empty", "", "empty"},
		{"missing", filepath.Join(home, "config", "missing"), "does not exist"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := GuardBaseWithHome(tc.base, home)
			if err == nil {
				t.Fatalf("expected the guard to refuse %q", tc.base)
			}
			var refused *RefusedError
			if !asRefused(err, &refused) {
				t.Fatalf("error is not a RefusedError: %v", err)
			}
			if !strings.Contains(refused.Reason, tc.reason) {
				t.Errorf("reason = %q, want to contain %q", refused.Reason, tc.reason)
			}
		})
	}
}

func TestGuardRequiresMarker(t *testing.T) {
	home := t.TempDir()
	base := filepath.Join(home, "config", "vpn")
	if err := os.MkdirAll(base, 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := GuardBaseWithHome(base, home); err == nil {
		t.Fatalf("expected the guard to refuse a directory without etc/agent.json")
	}
}

func TestGuardRefusesSymlinkBase(t *testing.T) {
	home := t.TempDir()
	real := filepath.Join(home, "real")
	if err := os.MkdirAll(filepath.Join(real, "etc"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(real, "etc", "agent.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(home, "link")
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	if _, err := GuardBaseWithHome(link, home); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("symlinked base dir must be refused, got %v", err)
	}
}

func TestGuardRefusesEscapingSymlinkParent(t *testing.T) {
	home := t.TempDir()
	outside := t.TempDir()
	baseOutside := filepath.Join(outside, "config", "vpn")
	if err := os.MkdirAll(filepath.Join(baseOutside, "etc"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(baseOutside, "etc", "agent.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(home, "escape")); err != nil {
		t.Fatal(err)
	}
	if _, err := GuardBaseWithHome(filepath.Join(home, "escape", "config", "vpn"), home); err == nil {
		t.Fatalf("a base dir escaping the home directory must be refused")
	}
}

// asRefused is a tiny errors.As helper to avoid importing errors in every test.
func asRefused(err error, target **RefusedError) bool {
	refused, ok := err.(*RefusedError)
	if ok {
		*target = refused
	}
	return ok
}
