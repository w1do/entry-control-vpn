package metrics

import (
	"context"
	"sort"
	"strings"
	"time"

	"log/slog"

	"vpn-agent/internal/api"
	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/logs"
	"vpn-agent/internal/state"
	"vpn-agent/internal/systemd"
)

// Options configures the metrics Collector.
type Options struct {
	Paths              config.Paths
	Probe              *XrayProbe
	Runner             execx.Runner
	SystemctlBin       string
	XrayUnit           string
	AgentUnit          string
	Logs               logs.Collector
	OnlineWindow       time.Duration
	AgentVersion       string
	IncludeDiagnostics bool
	ProcUptime         systemd.ProcUptime
	Logger             *slog.Logger
	Now                func() time.Time
}

// Collector gathers one metrics snapshot from Xray, systemd and the logs.
type Collector struct {
	o Options
}

// NewCollector returns a collector with defaults applied.
func NewCollector(options Options) *Collector {
	o := options
	if o.SystemctlBin == "" {
		o.SystemctlBin = "systemctl"
	}
	if o.XrayUnit == "" {
		o.XrayUnit = systemd.XrayUnitName
	}
	if o.AgentUnit == "" {
		o.AgentUnit = systemd.AgentUnitName
	}
	if o.OnlineWindow <= 0 {
		o.OnlineWindow = 2 * time.Minute
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.ProcUptime == nil {
		o.ProcUptime = systemd.DefaultProcUptime
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
	if o.Logs.AccessPath == "" {
		o.Logs.AccessPath = o.Paths.AccessLog
	}
	if o.Logs.ErrorPath == "" {
		o.Logs.ErrorPath = o.Paths.ErrorLog
	}
	return &Collector{o: o}
}

// Previous is the state needed to compute deltas and log offsets.
type Previous struct {
	Counters   map[string]int64
	LastSeen   map[string]time.Time
	LogOffsets state.LogOffsets
}

// Snapshot is one metrics collection result. It is always returned even when
// parts of the collection failed; Degraded lists the failed parts.
type Snapshot struct {
	Batch      *api.MetricsBatch
	Baseline   map[string]int64
	LogOffsets state.LogOffsets
	LastSeen   map[string]time.Time
	Degraded   []string
}

// Collect gathers the node metrics.
func (c *Collector) Collect(ctx context.Context, prev Previous, revision int64) (*Snapshot, error) {
	now := c.o.Now().UTC()
	snap := &Snapshot{
		Baseline:   CopyCounters(prev.Counters),
		LogOffsets: prev.LogOffsets,
		LastSeen:   map[string]time.Time{},
	}
	var degraded []string

	var unitStatus systemd.Status
	var unitErr error
	if c.o.Runner != nil {
		unitStatus, unitErr = systemd.Show(ctx, c.o.Runner, c.o.SystemctlBin, c.o.XrayUnit)
		if unitErr != nil {
			degraded = append(degraded, "systemctl show failed: "+unitErr.Error())
		}
	}
	xrayActive := unitErr == nil && unitStatus.Active()
	if unitErr == nil && !xrayActive {
		degraded = append(degraded, "xray unit is "+emptyDefault(unitStatus.ActiveState, "unknown"))
	}

	bootSeconds := 0.0
	if c.o.ProcUptime != nil {
		if seconds, err := c.o.ProcUptime(); err == nil {
			bootSeconds = seconds
		}
	}
	uptimeS := systemd.UptimeSeconds(unitStatus, bootSeconds)

	goroutines := 0
	if c.o.Probe != nil {
		if sysStats, err := c.o.Probe.SysStats(ctx); err == nil {
			goroutines = sysStats.Goroutines
			if uptimeS == 0 && sysStats.UptimeMinutes > 0 {
				uptimeS = sysStats.UptimeMinutes * 60
			}
		}
	}
	version := ""
	if c.o.Probe != nil {
		version = c.o.Probe.Version(ctx)
	}

	var current map[string]int64
	if c.o.Probe != nil {
		stats, err := c.o.Probe.StatsQuery(ctx, ScopeUser+">>>")
		if err != nil {
			degraded = append(degraded, "xray statsquery failed: "+err.Error())
		} else {
			current = make(map[string]int64, len(stats))
			for name, value := range stats {
				current[name] = value
			}
			if c.o.IncludeDiagnostics {
				for _, scope := range []string{ScopeInbound, ScopeOutbound} {
					extra, err := c.o.Probe.StatsQuery(ctx, scope+">>>")
					if err != nil {
						degraded = append(degraded, "xray "+scope+" statsquery failed: "+err.Error())
						continue
					}
					for name, value := range extra {
						current[name] = value
					}
				}
			}
		}
	}
	deltas := map[string]int64{}
	if current != nil {
		var baseline map[string]int64
		deltas, baseline = ComputeDeltas(prev.Counters, current)
		snap.Baseline = baseline
	}

	interval, logErr := c.o.Logs.Collect(prev.LogOffsets.Access, prev.LogOffsets.Error, now)
	if logErr != nil {
		degraded = append(degraded, "log collection failed: "+logErr.Error())
	}
	snap.LogOffsets = state.LogOffsets{Access: interval.AccessOffset, Error: interval.ErrorOffset}
	if interval.AccessTruncated || interval.ErrorTruncated {
		degraded = append(degraded, "xray log rotation or truncation detected")
	}

	online := Online{Emails: map[string][]string{}}
	if c.o.Probe != nil {
		if reported, err := c.o.Probe.OnlineUsers(ctx); err == nil {
			online = reported
		}
	}
	activeEmails := map[string]bool{}
	for email := range online.Emails {
		activeEmails[email] = true
		snap.LastSeen[email] = now
	}
	clientsTraffic := ClientTraffic(deltas)
	for email, traffic := range clientsTraffic {
		if traffic.Up != 0 || traffic.Down != 0 {
			activeEmails[email] = true
			snap.LastSeen[email] = now
		}
	}
	for email, ts := range prev.LastSeen {
		if ts.IsZero() {
			continue
		}
		if now.Sub(ts) <= c.o.OnlineWindow {
			activeEmails[email] = true
		}
		if now.Sub(ts) <= 24*time.Hour {
			if existing, ok := snap.LastSeen[email]; !ok || ts.After(existing) {
				snap.LastSeen[email] = ts
			}
		}
	}
	for email, ts := range interval.LastSeen {
		if ts.IsZero() {
			continue
		}
		if now.Sub(ts) <= c.o.OnlineWindow {
			activeEmails[email] = true
		}
		if existing, ok := snap.LastSeen[email]; !ok || ts.After(existing) {
			snap.LastSeen[email] = ts
		}
	}
	for email, ts := range snap.LastSeen {
		if now.Sub(ts) > 24*time.Hour {
			delete(snap.LastSeen, email)
		}
	}

	clientSet := map[string]bool{}
	for email := range clientsTraffic {
		clientSet[email] = true
	}
	for email := range activeEmails {
		clientSet[email] = true
	}
	emails := make([]string, 0, len(clientSet))
	for email := range clientSet {
		emails = append(emails, email)
	}
	sort.Strings(emails)
	clients := make([]api.ClientMetrics, 0, len(emails))
	for _, email := range emails {
		traffic := clientsTraffic[email]
		clients = append(clients, api.ClientMetrics{
			Email:  email,
			Up:     traffic.Up,
			Down:   traffic.Down,
			Online: activeEmails[email],
			IPs:    online.CloneIPs(email),
		})
	}

	batch := &api.MetricsBatch{
		TS:           now,
		Revision:     revision,
		AgentVersion: c.o.AgentVersion,
		Xray: api.XrayStatus{
			Active:     xrayActive,
			UptimeS:    uptimeS,
			Version:    version,
			PID:        unitStatus.MainPID,
			Goroutines: goroutines,
		},
		TrafficDelta: TrafficTotals(deltas),
		Clients:      clients,
		Connections: api.Connections{
			Accepted15s: interval.Counts.Accepted,
			Blocked15s:  interval.Counts.Blocked,
			Online:      len(activeEmails),
		},
		Errors: api.Errors{
			Rejected15s:  interval.Counts.Rejected,
			XrayError15s: interval.Errors,
		},
	}
	if c.o.IncludeDiagnostics {
		inbound := ScopeTraffic(deltas, ScopeInbound)
		outbound := ScopeTraffic(deltas, ScopeOutbound)
		if len(inbound) > 0 || len(outbound) > 0 {
			batch.Diagnostics = &api.Diagnostics{Inbound: inbound, Outbound: outbound}
		}
	}
	snap.Batch = batch
	snap.Degraded = degraded
	return snap, nil
}

// emptyDefault returns value or fallback when value is empty.
func emptyDefault(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
