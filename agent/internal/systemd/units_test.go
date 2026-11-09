package systemd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRenderAgentUnitHardening(t *testing.T) {
	unit := RenderAgentUnit(DefaultUnitOptions("/root/config/vpn"))
	required := []string{
		"NoNewPrivileges=yes",
		"ProtectSystem=strict",
		"ProtectHome=no",
		"ReadWritePaths=/root/config/vpn",
		"PrivateTmp=yes",
		"Restart=always",
		"UMask=0077",
		"ExecStart=/root/config/vpn/bin/vpn-agent serve",
	}
	for _, want := range required {
		if !strings.Contains(unit, want) {
			t.Errorf("agent unit is missing %q", want)
		}
	}
}

func TestRenderXrayUnitHardening(t *testing.T) {
	unit := RenderXrayUnit(DefaultUnitOptions("/root/config/vpn"))
	required := []string{
		"NoNewPrivileges=yes",
		"ProtectSystem=strict",
		"ReadWritePaths=/root/config/vpn",
		"PrivateTmp=yes",
		"ExecStart=/root/config/vpn/bin/xray run -c /root/config/vpn/etc/xray.json",
		"Environment=XRAY_LOCATION_ASSET=/root/config/vpn/etc",
		"Restart=on-failure",
	}
	for _, want := range required {
		if !strings.Contains(unit, want) {
			t.Errorf("xray unit is missing %q", want)
		}
	}
}

func TestPackagingTemplatesMatchRenderer(t *testing.T) {
	cases := []struct {
		file string
		want string
	}{
		{"vpn-agent.service", RenderAgentUnit(DefaultUnitOptions("/root/config/vpn"))},
		{"xray.service", RenderXrayUnit(DefaultUnitOptions("/root/config/vpn"))},
	}
	for _, tc := range cases {
		packaged, err := os.ReadFile(filepath.Join("..", "..", "packaging", tc.file))
		if err != nil {
			t.Fatalf("read packaging/%s: %v", tc.file, err)
		}
		if string(packaged) != tc.want {
			t.Errorf("packaging/%s diverges from the renderer\n--- packaged ---\n%s\n--- rendered ---\n%s", tc.file, packaged, tc.want)
		}
	}
}

func TestEscapeExecArg(t *testing.T) {
	if got := EscapeExecArg("/root/config/vpn/bin/xray"); got != "/root/config/vpn/bin/xray" {
		t.Errorf("plain path = %q", got)
	}
	if got := EscapeExecArg("/home/vpn user/config"); got != "\"/home/vpn user/config\"" {
		t.Errorf("spaced path = %q", got)
	}
	if got := EscapeExecArg("/srv/100%vpn"); !strings.Contains(got, "100%%vpn") {
		t.Errorf("percent escaping = %q", got)
	}
}

func TestWriteAndRemoveUnitFile(t *testing.T) {
	dir := t.TempDir()
	path, err := WriteUnitFile(dir, "test.service", "[Unit]\n")
	if err != nil {
		t.Fatalf("WriteUnitFile: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o644 {
		t.Errorf("unit mode = %04o", mode)
	}
	if err := RemoveUnitFile(dir, "test.service"); err != nil {
		t.Fatalf("RemoveUnitFile: %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("unit file still exists")
	}
	if err := RemoveUnitFile(dir, "test.service"); err != nil {
		t.Fatalf("RemoveUnitFile must ignore a missing file: %v", err)
	}
}
