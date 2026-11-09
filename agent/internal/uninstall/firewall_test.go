package uninstall

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"vpn-agent/internal/execx"
)

// writeTool writes a recording fake command that appends its arguments to calls.
func writeTool(t *testing.T, dir, name, body string) (string, string) {
	t.Helper()
	calls := filepath.Join(dir, name+".calls")
	script := "#!/bin/sh\necho \"$*\" >> " + calls + "\n" + body + "\n"
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
		t.Fatalf("write fake %s: %v", name, err)
	}
	return path, calls
}

func TestFirewallRemove(t *testing.T) {
	dir := t.TempDir()
	nft, nftCalls := writeTool(t, dir, "nft", `if [ "$1" = "list" ]; then
  printf 'table inet vpn_agent\n'
  printf 'table inet other\n'
  exit 0
fi
exit 0`)
	ufw, ufwCalls := writeTool(t, dir, "ufw", `if [ "$1" = "status" ]; then echo "Status: active"; fi
exit 0`)
	firewall := &Firewall{Exec: execx.OSRunner{}, NftBin: nft, UfwBin: ufw}
	steps := firewall.Remove(context.Background(), []int{443, 18443})
	if len(steps) != 2 {
		t.Fatalf("steps = %+v", steps)
	}
	statuses := map[string]string{}
	for _, step := range steps {
		statuses[step.Name] = step.Status
	}
	if statuses["firewall.nft"] != "ok" || statuses["firewall.ufw"] != "ok" {
		t.Fatalf("steps = %+v", steps)
	}
	nftLog, _ := os.ReadFile(nftCalls)
	if !strings.Contains(string(nftLog), "delete table inet vpn_agent") {
		t.Errorf("nft calls = %s", nftLog)
	}
	if strings.Contains(string(nftLog), "delete table inet other") {
		t.Errorf("unmanaged nft table was deleted: %s", nftLog)
	}
	ufwLog, _ := os.ReadFile(ufwCalls)
	if !strings.Contains(string(ufwLog), "delete allow 443/tcp") || !strings.Contains(string(ufwLog), "delete allow 18443/tcp") {
		t.Errorf("ufw calls = %s", ufwLog)
	}
}

func TestFirewallSkipsMissingTools(t *testing.T) {
	firewall := &Firewall{
		Exec:   execx.OSRunner{},
		NftBin: filepath.Join(t.TempDir(), "missing-nft"),
		UfwBin: filepath.Join(t.TempDir(), "missing-ufw"),
	}
	steps := firewall.Remove(context.Background(), []int{443})
	if len(steps) != 2 {
		t.Fatalf("steps = %+v", steps)
	}
	for _, step := range steps {
		if step.Status != "skipped" {
			t.Errorf("step %s status = %s", step.Name, step.Status)
		}
	}
}

func TestInboundPorts(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "xray.json")
	config := `{"inbounds":[{"port":443},{"port":18443},{"port":443}]}`
	if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	ports := InboundPorts(path)
	if len(ports) != 2 || ports[0] != 443 || ports[1] != 18443 {
		t.Fatalf("ports = %v", ports)
	}
	defaults := InboundPorts(filepath.Join(dir, "missing.json"))
	if len(defaults) != len(DefaultFirewallPorts) {
		t.Fatalf("defaults = %v", defaults)
	}
}

func TestParseNftTables(t *testing.T) {
	tables := parseNftTables([]byte("table inet vpn_agent\ntable ip filter\ngarbage line\n"))
	if len(tables) != 2 {
		t.Fatalf("tables = %+v", tables)
	}
	if tables[0].family != "inet" || tables[0].name != "vpn_agent" {
		t.Errorf("table = %+v", tables[0])
	}
	if !isManagedNftTable("vpn_agent") || !isManagedNftTable("vpn-agent-rules") {
		t.Errorf("managed table detection is wrong")
	}
	if isManagedNftTable("other") {
		t.Errorf("unmanaged table detected as managed")
	}
}
