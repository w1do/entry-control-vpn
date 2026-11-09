package uninstall

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"

	"log/slog"

	"vpn-agent/internal/execx"
)

// DefaultFirewallPorts are removed when the xray config cannot be read.
var DefaultFirewallPorts = []int{443, 18443}

// managedNftTablePrefixes are the table names created for the node.
var managedNftTablePrefixes = []string{"vpn_agent", "vpn-agent"}

// nftTableRe matches a line of "nft list tables".
var nftTableRe = regexp.MustCompile(`^table\s+(\S+)\s+(\S+)\s*$`)

// Firewall removes nft tables and ufw rules created for the node.
type Firewall struct {
	Exec   execx.Runner
	NftBin string
	UfwBin string
	Logger *slog.Logger
}

// Remove removes managed nft tables and ufw allow rules for the node ports.
func (f *Firewall) Remove(ctx context.Context, ports []int) []Step {
	var steps []Step
	if f.Exec == nil {
		return []Step{{Name: "firewall", Status: "skipped", Detail: "no command runner"}}
	}
	nftBin := f.NftBin
	if nftBin == "" {
		nftBin = "nft"
	}
	ufwBin := f.UfwBin
	if ufwBin == "" {
		ufwBin = "ufw"
	}

	res, err := f.Exec.Run(ctx, execx.Command{Name: nftBin, Args: []string{"list", "tables"}, Timeout: 15 * time.Second})
	switch {
	case err != nil:
		steps = append(steps, Step{Name: "firewall.nft", Status: "skipped", Detail: "nft not available: " + err.Error()})
	case !res.OK():
		steps = append(steps, Step{Name: "firewall.nft", Status: "skipped", Detail: fmt.Sprintf("nft list tables exited with %d", res.ExitCode)})
	default:
		removed := 0
		var failures []string
		for _, table := range parseNftTables(res.Stdout) {
			if !isManagedNftTable(table.name) {
				continue
			}
			delRes, delErr := f.Exec.Run(ctx, execx.Command{
				Name:    nftBin,
				Args:    []string{"delete", "table", table.family, table.name},
				Timeout: 15 * time.Second,
			})
			if delErr != nil || !delRes.OK() {
				failures = append(failures, table.family+" "+table.name)
				continue
			}
			removed++
		}
		status := "ok"
		detail := fmt.Sprintf("removed %d managed nft table(s)", removed)
		if len(failures) > 0 {
			status = "error"
			detail += "; failed: " + strings.Join(failures, ", ")
		}
		steps = append(steps, Step{Name: "firewall.nft", Status: status, Detail: detail})
	}

	ufwRes, err := f.Exec.Run(ctx, execx.Command{Name: ufwBin, Args: []string{"status"}, Timeout: 15 * time.Second})
	switch {
	case err != nil:
		steps = append(steps, Step{Name: "firewall.ufw", Status: "skipped", Detail: "ufw not available: " + err.Error()})
	case !ufwRes.OK():
		steps = append(steps, Step{Name: "firewall.ufw", Status: "skipped", Detail: fmt.Sprintf("ufw status exited with %d (probably inactive)", ufwRes.ExitCode)})
	default:
		removed := 0
		for _, port := range ports {
			if port <= 0 || port > 65535 {
				continue
			}
			for _, proto := range []string{"tcp", "udp"} {
				rule := fmt.Sprintf("%d/%s", port, proto)
				delRes, delErr := f.Exec.Run(ctx, execx.Command{
					Name:    ufwBin,
					Args:    []string{"delete", "allow", rule},
					Timeout: 15 * time.Second,
				})
				if delErr == nil && delRes.OK() {
					removed++
				}
			}
		}
		steps = append(steps, Step{Name: "firewall.ufw", Status: "ok", Detail: fmt.Sprintf("removed %d ufw allow rule(s)", removed)})
	}
	return steps
}

// nftTable is one parsed "table <family> <name>" line.
type nftTable struct {
	family string
	name   string
}

// parseNftTables parses "nft list tables" output.
func parseNftTables(output []byte) []nftTable {
	var tables []nftTable
	for _, line := range strings.Split(string(output), "\n") {
		matches := nftTableRe.FindStringSubmatch(strings.TrimSpace(line))
		if matches == nil {
			continue
		}
		tables = append(tables, nftTable{family: matches[1], name: matches[2]})
	}
	return tables
}

// isManagedNftTable reports whether the table belongs to the node agent.
func isManagedNftTable(name string) bool {
	lowered := strings.ToLower(name)
	for _, prefix := range managedNftTablePrefixes {
		if strings.HasPrefix(lowered, prefix) {
			return true
		}
	}
	return lowered == "vpn"
}

// InboundPorts returns the inbound ports of the installed xray config, or the
// contract defaults when the config is unavailable.
func InboundPorts(configPath string) []int {
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return append([]int(nil), DefaultFirewallPorts...)
	}
	var cfg struct {
		Inbounds []struct {
			Port int `json:"port"`
		} `json:"inbounds"`
	}
	if err := jsonUnmarshal(raw, &cfg); err != nil {
		return append([]int(nil), DefaultFirewallPorts...)
	}
	seen := map[int]bool{}
	for _, inbound := range cfg.Inbounds {
		if inbound.Port > 0 && inbound.Port <= 65535 {
			seen[inbound.Port] = true
		}
	}
	if len(seen) == 0 {
		return append([]int(nil), DefaultFirewallPorts...)
	}
	ports := make([]int, 0, len(seen))
	for port := range seen {
		ports = append(ports, port)
	}
	sort.Ints(ports)
	return ports
}
