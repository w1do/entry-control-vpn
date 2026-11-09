package runner

import (
	"context"
	"encoding/json"
	"os"
	"runtime"
	"strings"
	"time"

	"vpn-agent/internal/logs"
	"vpn-agent/internal/systemd"
)

// DiagnoseReport is the payload of POST /agent/v1/diagnose.
type DiagnoseReport struct {
	Timestamp      time.Time             `json:"timestamp"`
	AgentVersion   string                `json:"agent_version"`
	XrayVersion    string                `json:"xray_version"`
	Hostname       string                `json:"hostname"`
	OS             OSInfo                `json:"os"`
	BaseDir        string                `json:"base_dir"`
	Revision       int64                 `json:"revision"`
	ConfigSHA256   string                `json:"config_sha256,omitempty"`
	DegradedReason string                `json:"degraded_reason,omitempty"`
	Units          map[string]UnitReport `json:"units"`
	Health         HealthReport          `json:"health"`
	Logs           LogTails              `json:"logs"`
	Metrics        MetricsReport         `json:"metrics"`
}

// OSInfo describes the operating system of the node.
type OSInfo struct {
	ID         string `json:"id,omitempty"`
	Version    string `json:"version,omitempty"`
	PrettyName string `json:"pretty_name,omitempty"`
	Kernel     string `json:"kernel,omitempty"`
	Arch       string `json:"arch,omitempty"`
}

// UnitReport is the state of one systemd unit.
type UnitReport struct {
	Unit        string `json:"unit"`
	ActiveState string `json:"active_state"`
	SubState    string `json:"sub_state,omitempty"`
	MainPID     int    `json:"main_pid,omitempty"`
	UptimeS     int64  `json:"uptime_s,omitempty"`
}

// HealthReport is the result of a local health check.
type HealthReport struct {
	OK    bool   `json:"ok"`
	Error string `json:"error,omitempty"`
}

// LogTails contains the last log lines of the node.
type LogTails struct {
	Access []string `json:"access,omitempty"`
	Error  []string `json:"error,omitempty"`
	Apply  []string `json:"apply,omitempty"`
}

// MetricsReport summarises the local metrics state.
type MetricsReport struct {
	BufferedBatches int        `json:"buffered_batches"`
	LastMetricsAt   *time.Time `json:"last_metrics_at,omitempty"`
	Counters        int        `json:"counters"`
	LastSeenClients int        `json:"last_seen_clients"`
}

// Diagnose collects the diagnostic report without posting it.
func (r *Runner) Diagnose(ctx context.Context) (json.RawMessage, error) {
	now := r.now().UTC()
	snap := r.o.Store.Snapshot()
	paths := r.o.Config.Paths()
	hostname, _ := os.Hostname()

	report := DiagnoseReport{
		Timestamp:    now,
		AgentVersion: r.o.Version,
		XrayVersion:  r.xrayVersion(ctx),
		Hostname:     hostname,
		OS:           collectOSInfo(),
		BaseDir:      paths.BaseDir,
		Revision:     snap.Revision,
		ConfigSHA256: snap.ConfigSHA256,
		Units:        map[string]UnitReport{},
	}
	report.Metrics = MetricsReport{
		LastMetricsAt:   snap.LastMetricsAt,
		Counters:        len(snap.Counters),
		LastSeenClients: len(snap.LastSeen),
	}
	if count, err := r.o.Buffer.Len(); err == nil {
		report.Metrics.BufferedBatches = count
	}

	xrayActive := false
	for _, unit := range []string{r.o.Config.XrayUnit, r.o.Config.AgentUnit} {
		unitReport := UnitReport{Unit: unit}
		if status, err := systemd.Show(ctx, r.o.Exec, r.o.Config.SystemctlBin, unit); err == nil {
			unitReport.ActiveState = status.ActiveState
			unitReport.SubState = status.SubState
			unitReport.MainPID = status.MainPID
			bootSeconds := 0.0
			if r.o.ProcUptime != nil {
				if seconds, err := r.o.ProcUptime(); err == nil {
					bootSeconds = seconds
				}
			}
			unitReport.UptimeS = systemd.UptimeSeconds(status, bootSeconds)
			if unit == r.o.Config.XrayUnit && status.Active() {
				xrayActive = true
			}
		}
		report.Units[unit] = unitReport
	}

	health := HealthReport{OK: true}
	if err := r.o.Applier.HealthCheck(ctx); err != nil {
		health = HealthReport{OK: false, Error: err.Error()}
	}
	report.Health = health
	report.DegradedReason = r.degradedReason(xrayActive)

	accessTail, err := logs.Tail(paths.AccessLog, r.o.Config.DiagnoseLogLines())
	if err != nil {
		r.logger.Debug("could not read access log tail", "error", err)
	}
	errorTail, err := logs.Tail(paths.ErrorLog, r.o.Config.DiagnoseLogLines())
	if err != nil {
		r.logger.Debug("could not read xray error log tail", "error", err)
	}
	applyTail, err := logs.Tail(paths.ApplyLog, r.o.Config.DiagnoseLogLines())
	if err != nil {
		r.logger.Debug("could not read apply log tail", "error", err)
	}
	report.Logs = LogTails{Access: accessTail, Error: errorTail, Apply: applyTail}

	data, err := json.Marshal(unescapeReport(report))
	if err != nil {
		return nil, err
	}
	return json.RawMessage(data), nil
}

// unescapeReport is a no-op used to keep Diagnose readable when changing the
// report type; it exists so the JSON shape is defined in exactly one place.
func unescapeReport(report DiagnoseReport) DiagnoseReport { return report }

// collectOSInfo reads the operating system identity.
func collectOSInfo() OSInfo {
	info := OSInfo{Arch: runtime.GOARCH}
	if raw, err := os.ReadFile("/etc/os-release"); err == nil {
		for _, line := range strings.Split(string(raw), "\n") {
			key, value, ok := strings.Cut(strings.TrimSpace(line), "=")
			if !ok {
				continue
			}
			value = strings.Trim(value, "\"")
			switch key {
			case "ID":
				info.ID = value
			case "VERSION_ID":
				info.Version = value
			case "PRETTY_NAME":
				info.PrettyName = value
			}
		}
	}
	if raw, err := os.ReadFile("/proc/sys/kernel/osrelease"); err == nil {
		info.Kernel = strings.TrimSpace(string(raw))
	}
	return info
}
