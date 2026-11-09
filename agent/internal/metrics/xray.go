package metrics

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"

	"log/slog"

	"vpn-agent/internal/api"
	"vpn-agent/internal/execx"
)

// DefaultXrayAPITimeout bounds one "xray api" call.
const DefaultXrayAPITimeout = 5 * time.Second

// XrayProbe calls the local Xray CLI (API commands and version).
type XrayProbe struct {
	Exec     execx.Runner
	Binary   string
	APIAddr  string
	AssetDir string
	Timeout  time.Duration
	Logger   *slog.Logger

	mu      sync.Mutex
	version string
}

// NewXrayProbe returns a probe for the given xray binary and API address.
func NewXrayProbe(runner execx.Runner, binary, apiAddr, assetDir string, logger *slog.Logger) *XrayProbe {
	if logger == nil {
		logger = slog.Default()
	}
	return &XrayProbe{
		Exec:     runner,
		Binary:   binary,
		APIAddr:  apiAddr,
		AssetDir: assetDir,
		Timeout:  DefaultXrayAPITimeout,
		Logger:   logger,
	}
}

// env returns the environment additions for xray subprocesses.
func (p *XrayProbe) env() []string {
	if p.AssetDir == "" {
		return nil
	}
	return []string{"XRAY_LOCATION_ASSET=" + p.AssetDir}
}

// timeout returns the configured API timeout.
func (p *XrayProbe) timeout() time.Duration {
	if p.Timeout <= 0 {
		return DefaultXrayAPITimeout
	}
	return p.Timeout
}

// apiArgs builds "api <command> --server=... -timeout=N ..." arguments.
func (p *XrayProbe) apiArgs(command string, extra ...string) []string {
	seconds := int(p.timeout() / time.Second)
	if seconds < 1 {
		seconds = 1
	}
	args := []string{"api", command, "--server=" + p.APIAddr, "-timeout", strconv.Itoa(seconds)}
	return append(args, extra...)
}

// run executes xray with the probe environment.
func (p *XrayProbe) run(ctx context.Context, timeout time.Duration, args ...string) (execx.Result, error) {
	return p.Exec.Run(ctx, execx.Command{
		Name:    p.Binary,
		Args:    args,
		Env:     p.env(),
		Timeout: timeout,
	})
}

// Version returns the Xray version, cached after the first successful call.
func (p *XrayProbe) Version(ctx context.Context) string {
	p.mu.Lock()
	cached := p.version
	p.mu.Unlock()
	if cached != "" {
		return cached
	}
	res, err := p.run(ctx, 10*time.Second, "version")
	if err != nil || !res.OK() {
		return ""
	}
	version := ParseXrayVersion(res.Stdout)
	if version == "" {
		return ""
	}
	p.mu.Lock()
	p.version = version
	p.mu.Unlock()
	return version
}

// InvalidateVersion drops the cached Xray version (after an upgrade).
func (p *XrayProbe) InvalidateVersion() {
	p.mu.Lock()
	p.version = ""
	p.mu.Unlock()
}

// ParseXrayVersion extracts the version from "xray version" output.
func ParseXrayVersion(output []byte) string {
	for _, line := range strings.Split(string(output), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && strings.EqualFold(fields[0], "Xray") {
			return fields[1]
		}
	}
	return ""
}

// StatsQuery runs "xray api statsquery" and parses the counters.
func (p *XrayProbe) StatsQuery(ctx context.Context, pattern string) (map[string]int64, error) {
	res, err := p.run(ctx, p.timeout(), p.apiArgs("statsquery", "-pattern", pattern)...)
	if err != nil {
		return nil, fmt.Errorf("metrics: statsquery: %w", err)
	}
	if !res.OK() {
		return nil, fmt.Errorf("metrics: statsquery exited with %d: %s", res.ExitCode, res.Output())
	}
	stats, err := ParseStatsQuery(res.Stdout)
	if err != nil {
		return nil, err
	}
	return stats, nil
}

// SysStats runs "xray api statssys".
func (p *XrayProbe) SysStats(ctx context.Context) (SysStats, error) {
	res, err := p.run(ctx, p.timeout(), p.apiArgs("statssys")...)
	if err != nil {
		return SysStats{}, fmt.Errorf("metrics: statssys: %w", err)
	}
	if !res.OK() {
		return SysStats{}, fmt.Errorf("metrics: statssys exited with %d: %s", res.ExitCode, res.Output())
	}
	return ParseSysStats(res.Stdout), nil
}

// OnlineUsers returns the online clients reported by the Xray stats service.
// Unsupported commands yield an empty result so the caller can fall back to
// last-seen tracking.
func (p *XrayProbe) OnlineUsers(ctx context.Context) (Online, error) {
	attempts := [][]string{
		p.apiArgs("statsonlineiplist", "-all", "-include-traffic"),
		p.apiArgs("statsonlineiplist", "-all"),
		p.apiArgs("statsgetallonlineusers"),
	}
	for _, args := range attempts {
		res, err := p.run(ctx, p.timeout(), args...)
		if err != nil || !res.OK() {
			continue
		}
		online := ParseOnlineUsers(res.Stdout)
		if online.Count() > 0 {
			return online, nil
		}
	}
	return Online{Emails: map[string][]string{}}, nil
}

// InboundUserEmails returns the users present in one inbound according to
// "xray api inbounduser". It is used to detect desired-state drift after an
// apply; an unsupported command returns an error that callers treat as
// inconclusive.
func (p *XrayProbe) InboundUserEmails(ctx context.Context, tag string) ([]string, error) {
	res, err := p.run(ctx, p.timeout(), p.apiArgs("inbounduser", "-tag", tag)...)
	if err != nil {
		return nil, fmt.Errorf("metrics: inbounduser: %w", err)
	}
	if !res.OK() {
		return nil, fmt.Errorf("metrics: inbounduser exited with %d: %s", res.ExitCode, res.Output())
	}
	return api.ParseXrayEmails(res.Stdout), nil
}
