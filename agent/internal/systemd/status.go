// Package systemd renders hardened systemd units and talks to systemctl.
package systemd

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"vpn-agent/internal/execx"
)

// DefaultUnitDir is where packaged units are installed.
const DefaultUnitDir = "/etc/systemd/system"

// Unit names managed by the agent.
const (
	AgentUnitName = "vpn-agent.service"
	XrayUnitName  = "xray.service"
)

// Status is the subset of "systemctl show" properties the agent needs.
type Status struct {
	Unit                          string
	LoadState                     string
	ActiveState                   string
	SubState                      string
	MainPID                       int
	ActiveEnterTimestampMonotonic int64 // microseconds since boot
}

// Active reports whether the unit is in the active state.
func (s Status) Active() bool { return s.ActiveState == "active" }

// ProcUptime returns the seconds since boot (the first field of /proc/uptime).
type ProcUptime func() (float64, error)

// DefaultProcUptime reads /proc/uptime.
func DefaultProcUptime() (float64, error) {
	raw, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0, err
	}
	fields := strings.Fields(string(raw))
	if len(fields) == 0 {
		return 0, fmt.Errorf("systemd: unexpected /proc/uptime contents")
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return 0, fmt.Errorf("systemd: parse /proc/uptime: %w", err)
	}
	return value, nil
}

// Show reads the status of one unit. The systemctl binary is configurable so
// the agent can be tested with a fake script.
func Show(ctx context.Context, runner execx.Runner, systemctlBin, unit string) (Status, error) {
	args := []string{
		"show", unit,
		"--property=LoadState",
		"--property=ActiveState",
		"--property=SubState",
		"--property=MainPID",
		"--property=ActiveEnterTimestampMonotonic",
	}
	res, err := runner.Run(ctx, execx.Command{
		Name:    systemctlBin,
		Args:    args,
		Timeout: 10 * time.Second,
	})
	if err != nil {
		return Status{Unit: unit}, fmt.Errorf("systemd: show %s: %w", unit, err)
	}
	if !res.OK() {
		return Status{Unit: unit}, fmt.Errorf("systemd: show %s exited with %d: %s", unit, res.ExitCode, res.Output())
	}
	status := ParseShow(res.Stdout)
	status.Unit = unit
	return status, nil
}

// ParseShow parses Key=Value output of "systemctl show".
func ParseShow(output []byte) Status {
	var status Status
	for _, line := range strings.Split(string(output), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		switch key {
		case "LoadState":
			status.LoadState = value
		case "ActiveState":
			status.ActiveState = value
		case "SubState":
			status.SubState = value
		case "MainPID":
			if pid, err := strconv.Atoi(strings.TrimSpace(value)); err == nil {
				status.MainPID = pid
			}
		case "ActiveEnterTimestampMonotonic":
			if usec, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64); err == nil {
				status.ActiveEnterTimestampMonotonic = usec
			}
		}
	}
	return status
}

// UptimeSeconds computes the unit uptime from its monotonic active-enter
// timestamp and the current boot duration.
func UptimeSeconds(status Status, bootSeconds float64) int64 {
	if status.ActiveEnterTimestampMonotonic <= 0 || bootSeconds <= 0 {
		return 0
	}
	uptime := bootSeconds - float64(status.ActiveEnterTimestampMonotonic)/1e6
	if uptime < 0 {
		return 0
	}
	return int64(uptime)
}

// IsActive reports whether the unit is active. An inactive unit is not an
// error (systemctl exits with status 3).
func IsActive(ctx context.Context, runner execx.Runner, systemctlBin, unit string) (bool, error) {
	res, err := runner.Run(ctx, execx.Command{
		Name:    systemctlBin,
		Args:    []string{"is-active", unit},
		Timeout: 10 * time.Second,
	})
	if err != nil {
		return false, fmt.Errorf("systemd: is-active %s: %w", unit, err)
	}
	if res.OK() {
		return true, nil
	}
	return false, nil
}

// Restart restarts one unit and waits for the command to finish.
func Restart(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "restart", unit)
}

// RestartNoBlock asks systemd to restart the unit without waiting for it to
// finish. Used to restart the agent itself.
func RestartNoBlock(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "--no-block", "restart", unit)
}

// Stop stops one unit.
func Stop(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "stop", unit)
}

// StopNoBlock asks systemd to stop the unit without waiting for the process to
// exit. The agent uses it to stop its own unit after a panel uninstall command.
func StopNoBlock(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "--no-block", "stop", unit)
}

// Disable disables one unit.
func Disable(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "disable", unit)
}

// Enable enables one unit.
func Enable(ctx context.Context, runner execx.Runner, systemctlBin, unit string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "enable", unit)
}

// DaemonReload reloads unit files.
func DaemonReload(ctx context.Context, runner execx.Runner, systemctlBin string, timeout time.Duration) error {
	return runUnitCommand(ctx, runner, systemctlBin, timeout, "daemon-reload")
}

// ResetFailed clears the failed state of the given units.
func ResetFailed(ctx context.Context, runner execx.Runner, systemctlBin string, timeout time.Duration, units ...string) error {
	args := append([]string{"reset-failed"}, units...)
	return runUnitCommand(ctx, runner, systemctlBin, timeout, args...)
}

// runUnitCommand runs a systemctl subcommand, turning a non-zero exit into an error.
func runUnitCommand(ctx context.Context, runner execx.Runner, systemctlBin string, timeout time.Duration, args ...string) error {
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	res, err := runner.Run(ctx, execx.Command{Name: systemctlBin, Args: args, Timeout: timeout})
	if err != nil {
		return fmt.Errorf("systemd: %s: %w", strings.Join(args, " "), err)
	}
	if !res.OK() {
		return fmt.Errorf("systemd: %s exited with %d: %s", strings.Join(args, " "), res.ExitCode, res.Output())
	}
	return nil
}
