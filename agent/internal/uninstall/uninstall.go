package uninstall

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"log/slog"

	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/systemd"
)

// Step is one action performed by the uninstall procedure.
type Step struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
}

// Report is the result of a successful uninstall run.
type Report struct {
	BaseDir       string `json:"base_dir"`
	FirewallPorts []int  `json:"firewall_ports,omitempty"`
	Steps         []Step `json:"steps"`
}

// Options configures an Uninstaller.
type Options struct {
	Paths         config.Paths
	Exec          execx.Runner
	Logger        *slog.Logger
	HomeDir       string
	UnitDir       string
	AgentUnit     string
	XrayUnit      string
	SystemctlBin  string
	NftBin        string
	UfwBin        string
	FirewallPorts []int
	// StopAgent stops vpn-agent.service before files are removed. It must be
	// false when the uninstaller runs inside the agent process itself (panel
	// command): the runner stops its own unit after reporting the result.
	StopAgent bool
	Now       func() time.Time
}

// Uninstaller removes every trace of the node.
type Uninstaller struct {
	o       Options
	homeDir string
}

// New returns an Uninstaller with defaults applied.
func New(o Options) *Uninstaller {
	if o.UnitDir == "" {
		o.UnitDir = systemd.DefaultUnitDir
	}
	if o.AgentUnit == "" {
		o.AgentUnit = systemd.AgentUnitName
	}
	if o.XrayUnit == "" {
		o.XrayUnit = systemd.XrayUnitName
	}
	if o.SystemctlBin == "" {
		o.SystemctlBin = "systemctl"
	}
	if o.NftBin == "" {
		o.NftBin = "nft"
	}
	if o.UfwBin == "" {
		o.UfwBin = "ufw"
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	home := o.HomeDir
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	return &Uninstaller{o: o, homeDir: home}
}

// Validate runs the destructive-path guard. It must be called before anything
// is removed; Run calls it again before the first side effect.
func (u *Uninstaller) Validate() (string, error) {
	return GuardBaseWithHome(u.o.Paths.BaseDir, u.homeDir)
}

// Run stops and disables the units, removes firewall rules, unit files and the
// base directory. The agent must not stop its own unit here: it exits after
// reporting success, and the unit has already been disabled.
func (u *Uninstaller) Run(ctx context.Context) (Report, error) {
	resolved, err := u.Validate()
	if err != nil {
		return Report{}, err
	}
	report := Report{BaseDir: resolved}
	ports := u.o.FirewallPorts
	if len(ports) == 0 {
		ports = InboundPorts(u.o.Paths.XrayConfig)
	}
	report.FirewallPorts = ports

	firewall := &Firewall{Exec: u.o.Exec, NftBin: u.o.NftBin, UfwBin: u.o.UfwBin, Logger: u.o.Logger}
	report.Steps = append(report.Steps, firewall.Remove(ctx, ports)...)

	report.Steps = append(report.Steps,
		u.systemctlStep(ctx, "stop xray", func() error {
			return systemd.Stop(ctx, u.o.Exec, u.o.SystemctlBin, u.o.XrayUnit, 60*time.Second)
		}),
	)
	if u.o.StopAgent {
		report.Steps = append(report.Steps, u.systemctlStep(ctx, "stop agent", func() error {
			return systemd.Stop(ctx, u.o.Exec, u.o.SystemctlBin, u.o.AgentUnit, 60*time.Second)
		}))
	}
	report.Steps = append(report.Steps,
		u.systemctlStep(ctx, "disable xray", func() error {
			return systemd.Disable(ctx, u.o.Exec, u.o.SystemctlBin, u.o.XrayUnit, 30*time.Second)
		}),
		u.systemctlStep(ctx, "disable agent", func() error {
			return systemd.Disable(ctx, u.o.Exec, u.o.SystemctlBin, u.o.AgentUnit, 30*time.Second)
		}),
		u.removeUnitStep(u.o.AgentUnit),
		u.removeUnitStep(u.o.XrayUnit),
		u.systemctlStep(ctx, "daemon-reload", func() error {
			return systemd.DaemonReload(ctx, u.o.Exec, u.o.SystemctlBin, 30*time.Second)
		}),
		u.systemctlStep(ctx, "reset-failed", func() error {
			return systemd.ResetFailed(ctx, u.o.Exec, u.o.SystemctlBin, 30*time.Second, u.o.AgentUnit, u.o.XrayUnit)
		}),
	)

	if err := os.RemoveAll(resolved); err != nil {
		step := Step{Name: "remove base dir", Status: "error", Detail: err.Error()}
		report.Steps = append(report.Steps, step)
		return report, fmt.Errorf("uninstall: remove %s: %w", resolved, err)
	}
	report.Steps = append(report.Steps, Step{Name: "remove base dir", Status: "ok", Detail: resolved})
	return report, nil
}

// systemctlStep wraps a systemctl operation into a report step.
func (u *Uninstaller) systemctlStep(ctx context.Context, name string, fn func() error) Step {
	if err := fn(); err != nil {
		return Step{Name: name, Status: "error", Detail: err.Error()}
	}
	return Step{Name: name, Status: "ok"}
}

// removeUnitStep removes one unit file.
func (u *Uninstaller) removeUnitStep(unit string) Step {
	if err := systemd.RemoveUnitFile(u.o.UnitDir, unit); err != nil {
		return Step{Name: "remove unit " + unit, Status: "error", Detail: err.Error()}
	}
	return Step{Name: "remove unit " + unit, Status: "ok", Detail: systemd.UnitPath(u.o.UnitDir, unit)}
}

// jsonUnmarshal is a tiny indirection so firewall.go keeps a small import list.
func jsonUnmarshal(data []byte, v any) error { return json.Unmarshal(data, v) }
