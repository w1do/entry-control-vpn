// Package apply implements the atomic Xray configuration pipeline:
// validate -> swap -> restart -> health-check -> rollback.
package apply

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"log/slog"

	"vpn-agent/internal/api"
	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/metrics"
	"vpn-agent/internal/systemd"
)

// Default tuning values used when Options leave them zero.
const (
	DefaultHealthTimeout      = 15 * time.Second
	DefaultHealthPollInterval = 500 * time.Millisecond
	DefaultTestTimeout        = 60 * time.Second
	DefaultRestartTimeout     = 60 * time.Second
	DefaultApplyLogMaxBytes   = 256 << 10
	DefaultResultLogTailBytes = 4096
	DefaultRollbackActiveWait = 10 * time.Second
)

// Options configures an Applier.
type Options struct {
	Paths        config.Paths
	Exec         execx.Runner
	Logger       *slog.Logger
	XrayBin      string
	SystemctlBin string
	XrayUnit     string
	XrayAPIAddr  string
	AssetDir     string
	Probe        *metrics.XrayProbe

	HealthTimeout      time.Duration
	HealthPollInterval time.Duration
	TestTimeout        time.Duration
	RestartTimeout     time.Duration
}

// Applier applies configuration revisions to the node.
type Applier struct {
	o     Options
	probe *metrics.XrayProbe
}

// Result is the outcome of one Apply call.
type Result struct {
	Status         string
	Error          string
	LogTail        string
	SHA256         string
	Backup         bool
	DegradedReason string
}

// Applied reports whether the configuration was applied successfully.
func (r Result) Applied() bool { return r.Status == api.StatusApplied }

// New returns an Applier, filling defaults.
func New(o Options) *Applier {
	if o.HealthTimeout <= 0 {
		o.HealthTimeout = DefaultHealthTimeout
	}
	if o.HealthPollInterval <= 0 {
		o.HealthPollInterval = DefaultHealthPollInterval
	}
	if o.TestTimeout <= 0 {
		o.TestTimeout = DefaultTestTimeout
	}
	if o.RestartTimeout <= 0 {
		o.RestartTimeout = DefaultRestartTimeout
	}
	if o.SystemctlBin == "" {
		o.SystemctlBin = "systemctl"
	}
	if o.XrayUnit == "" {
		o.XrayUnit = systemd.XrayUnitName
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
	probe := o.Probe
	if probe == nil && o.Exec != nil {
		probe = metrics.NewXrayProbe(o.Exec, o.XrayBin, o.XrayAPIAddr, o.AssetDir, o.Logger)
	}
	return &Applier{o: o, probe: probe}
}

// Apply validates, swaps, restarts and health-checks a configuration revision.
// It never returns an error: failures are reported through Result so the
// caller can always report the outcome to the panel.
func (a *Applier) Apply(ctx context.Context, revision int64, raw []byte, expectedSHA string) Result {
	logs := &strings.Builder{}
	appendStep := func(format string, args ...any) {
		line := fmt.Sprintf(format, args...)
		logs.WriteString(line)
		logs.WriteString("\n")
		a.appendApplyLog(revision, line)
	}
	base := Result{Status: api.StatusFailed, SHA256: sha256Hex(raw)}

	if !json.Valid(raw) {
		msg := "config is not valid JSON"
		appendStep("validate: %s", msg)
		return a.fail(base, msg, logs)
	}
	computed := sha256Hex(raw)
	if expectedSHA != "" {
		switch {
		case strings.EqualFold(expectedSHA, computed):
			appendStep("validate: sha256 verified")
		default:
			canonical, canonicalOK := canonicalSHA256(raw)
			if canonicalOK && strings.EqualFold(expectedSHA, canonical) {
				appendStep("validate: sha256 verified (canonical form)")
			} else {
				canonicalText := "unavailable"
				if canonicalOK {
					canonicalText = canonical
				}
				// The panel hashes the canonical JSON form produced by the
				// compiler while the HTTP body may be re-serialized. A
				// mismatch is therefore a warning, not a rejection: the
				// candidate is still validated by xray -test and health check.
				appendStep("validate: warning: sha256 mismatch (panel %s, raw %s, canonical %s); continuing",
					expectedSHA, computed, canonicalText)
			}
		}
	}
	appendStep("validate: config sha256 %s", computed)

	candidate := a.o.Paths.XrayConfigNew
	if err := os.MkdirAll(filepath.Dir(candidate), 0o700); err != nil {
		msg := fmt.Sprintf("create config dir: %v", err)
		appendStep("candidate: %s", msg)
		return a.fail(base, msg, logs)
	}
	if err := writeFileAtomic(candidate, raw, 0o600); err != nil {
		msg := fmt.Sprintf("write candidate config: %v", err)
		appendStep("candidate: %s", msg)
		return a.fail(base, msg, logs)
	}
	appendStep("candidate: wrote %s", candidate)

	testRes, err := a.o.Exec.Run(ctx, execx.Command{
		Name:    a.o.XrayBin,
		Args:    []string{"run", "-test", "-c", candidate},
		Env:     a.xrayEnv(),
		Timeout: a.o.TestTimeout,
	})
	testOutput := testRes.Output()
	if err != nil {
		appendStep("test: error: %v", err)
		_ = os.Remove(candidate)
		return a.fail(base, fmt.Sprintf("xray -test failed: %v", err), logs)
	}
	if !testRes.OK() {
		msg := fmt.Sprintf("xray -test exited with %d: %s", testRes.ExitCode, firstMeaningfulLine(testOutput))
		appendStep("test: %s", msg)
		_ = os.Remove(candidate)
		base.Error = msg
		base.LogTail = tailString(logs.String()+"\n"+tailString(testOutput, DefaultResultLogTailBytes), DefaultResultLogTailBytes)
		return base
	}
	appendStep("test: ok (%s)", testOutput)

	hadBackup := false
	if _, statErr := os.Stat(a.o.Paths.XrayConfig); statErr == nil {
		prev, err := os.ReadFile(a.o.Paths.XrayConfig)
		if err != nil {
			msg := fmt.Sprintf("read current config: %v", err)
			appendStep("backup: %s", msg)
			_ = os.Remove(candidate)
			return a.fail(base, msg, logs)
		}
		if err := writeFileAtomic(a.o.Paths.XrayBackup, prev, 0o600); err != nil {
			msg := fmt.Sprintf("write backup config: %v", err)
			appendStep("backup: %s", msg)
			_ = os.Remove(candidate)
			return a.fail(base, msg, logs)
		}
		hadBackup = true
		appendStep("backup: saved previous config to %s", a.o.Paths.XrayBackup)
	} else if !errors.Is(statErr, os.ErrNotExist) {
		msg := fmt.Sprintf("stat current config: %v", statErr)
		appendStep("backup: %s", msg)
		_ = os.Remove(candidate)
		return a.fail(base, msg, logs)
	} else {
		appendStep("backup: no previous config (first install)")
	}
	base.Backup = hadBackup

	if err := os.Rename(candidate, a.o.Paths.XrayConfig); err != nil {
		msg := fmt.Sprintf("atomic rename of candidate config: %v", err)
		appendStep("swap: %s", msg)
		_ = os.Remove(candidate)
		return a.fail(base, msg, logs)
	}
	if err := os.Chmod(a.o.Paths.XrayConfig, 0o600); err != nil {
		appendStep("swap: chmod warning: %v", err)
	}
	appendStep("swap: installed %s", a.o.Paths.XrayConfig)

	if err := systemd.Restart(ctx, a.o.Exec, a.o.SystemctlBin, a.o.XrayUnit, a.o.RestartTimeout); err != nil {
		msg := fmt.Sprintf("systemctl restart %s: %v", a.o.XrayUnit, err)
		appendStep("restart: %s", msg)
		return a.rollback(ctx, revision, msg, hadBackup, base, logs)
	}
	appendStep("restart: ok")

	if err := a.waitHealthy(ctx, raw); err != nil {
		msg := fmt.Sprintf("health check: %v", err)
		appendStep("health: %s", msg)
		return a.rollback(ctx, revision, msg, hadBackup, base, logs)
	}
	appendStep("health: ok")

	degraded := a.verifyClients(ctx, raw)
	if degraded != "" {
		appendStep("clients: %s", degraded)
	} else {
		appendStep("clients: desired state matches running inbound users")
	}

	base.Status = api.StatusApplied
	base.DegradedReason = degraded
	base.LogTail = tailString(logs.String(), DefaultResultLogTailBytes)
	return base
}

// Restart restarts Xray and waits for it to become healthy using the current
// on-disk configuration.
func (a *Applier) Restart(ctx context.Context) error {
	if err := systemd.Restart(ctx, a.o.Exec, a.o.SystemctlBin, a.o.XrayUnit, a.o.RestartTimeout); err != nil {
		return err
	}
	raw, err := os.ReadFile(a.o.Paths.XrayConfig)
	if err != nil {
		return fmt.Errorf("read current config: %w", err)
	}
	return a.waitHealthy(ctx, raw)
}

// HealthCheck verifies the running node against the current configuration.
func (a *Applier) HealthCheck(ctx context.Context) error {
	raw, err := os.ReadFile(a.o.Paths.XrayConfig)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return errors.New("no configuration installed")
		}
		return fmt.Errorf("read current config: %w", err)
	}
	return a.probeHealth(ctx, raw)
}

// waitHealthy polls the node until it reports healthy or the deadline expires.
func (a *Applier) waitHealthy(ctx context.Context, raw []byte) error {
	deadline := time.Now().Add(a.o.HealthTimeout)
	var lastErr error
	for {
		lastErr = a.probeHealth(ctx, raw)
		if lastErr == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("not healthy after %s: %w", a.o.HealthTimeout, lastErr)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(a.o.HealthPollInterval):
		}
	}
}

// probeHealth checks the unit state, inbound ports and (when configured) the
// Xray stats API.
func (a *Applier) probeHealth(ctx context.Context, raw []byte) error {
	status, err := systemd.Show(ctx, a.o.Exec, a.o.SystemctlBin, a.o.XrayUnit)
	if err != nil {
		return err
	}
	if !status.Active() {
		return fmt.Errorf("unit %s is %s", a.o.XrayUnit, emptyOr(status.ActiveState, "unknown"))
	}
	targets := parseHealthTargets(raw)
	for _, addr := range targets.Ports {
		conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
		if err != nil {
			return fmt.Errorf("dial %s: %w", addr, err)
		}
		_ = conn.Close()
	}
	if targets.VerifyStats && targets.APIAddr != "" {
		res, err := a.o.Exec.Run(ctx, execx.Command{
			Name:    a.o.XrayBin,
			Args:    []string{"api", "statsquery", "--server=" + targets.APIAddr, "-timeout", "3", "-pattern", ""},
			Env:     a.xrayEnv(),
			Timeout: 5 * time.Second,
		})
		if err != nil {
			return fmt.Errorf("stats api: %w", err)
		}
		if !res.OK() {
			return fmt.Errorf("stats api exited with %d: %s", res.ExitCode, res.Output())
		}
	}
	return nil
}

// rollback restores the previous configuration and restarts Xray.
func (a *Applier) rollback(ctx context.Context, revision int64, reason string, hadBackup bool, base Result, logs *strings.Builder) Result {
	appendStep := func(format string, args ...any) {
		line := fmt.Sprintf(format, args...)
		logs.WriteString(line)
		logs.WriteString("\n")
		a.appendApplyLog(revision, line)
	}
	var problems []string
	if hadBackup {
		if err := restoreFile(a.o.Paths.XrayBackup, a.o.Paths.XrayConfig); err != nil {
			problems = append(problems, fmt.Sprintf("restore backup: %v", err))
			appendStep("rollback: restore backup failed: %v", err)
		} else {
			appendStep("rollback: restored %s from backup", a.o.Paths.XrayConfig)
		}
	} else {
		if err := os.Remove(a.o.Paths.XrayConfig); err != nil && !errors.Is(err, os.ErrNotExist) {
			problems = append(problems, fmt.Sprintf("remove failed config: %v", err))
			appendStep("rollback: remove failed config: %v", err)
		} else {
			appendStep("rollback: removed failed first-install config")
		}
	}
	if err := systemd.Restart(ctx, a.o.Exec, a.o.SystemctlBin, a.o.XrayUnit, a.o.RestartTimeout); err != nil {
		problems = append(problems, fmt.Sprintf("restart after rollback: %v", err))
		appendStep("rollback: restart failed: %v", err)
	} else {
		appendStep("rollback: restart issued")
		if err := a.waitActive(ctx, DefaultRollbackActiveWait); err != nil {
			problems = append(problems, err.Error())
			appendStep("rollback: unit did not become active: %v", err)
		} else {
			appendStep("rollback: unit is active")
		}
	}
	errText := reason
	if len(problems) > 0 {
		errText = reason + "; rollback problems: " + strings.Join(problems, "; ")
	}
	base.Status = api.StatusFailed
	base.Error = errText
	base.LogTail = tailString(logs.String(), DefaultResultLogTailBytes)
	return base
}

// waitActive waits until the unit reports the active state.
func (a *Applier) waitActive(ctx context.Context, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for {
		active, err := systemd.IsActive(ctx, a.o.Exec, a.o.SystemctlBin, a.o.XrayUnit)
		if err == nil && active {
			return nil
		}
		if time.Now().After(deadline) {
			if err != nil {
				return fmt.Errorf("check unit state: %w", err)
			}
			return fmt.Errorf("unit %s is not active after %s", a.o.XrayUnit, timeout)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(a.o.HealthPollInterval):
		}
	}
}

// verifyClients compares the desired client list from the config with the
// users reported by the running Xray inbound API. Differences yield a degraded
// reason; an unsupported API is ignored.
func (a *Applier) verifyClients(ctx context.Context, raw []byte) string {
	if a.probe == nil {
		return ""
	}
	expected := parseClientEmails(raw)
	if len(expected) == 0 {
		return ""
	}
	tags := make([]string, 0, len(expected))
	for tag := range expected {
		tags = append(tags, tag)
	}
	sort.Strings(tags)
	var problems []string
	verified := 0
	for _, tag := range tags {
		actual, err := a.probe.InboundUserEmails(ctx, tag)
		if err != nil {
			continue
		}
		verified++
		if !sameStringSet(expected[tag], actual) {
			problems = append(problems, fmt.Sprintf("inbound %s: expected %d users, running %d", tag, len(expected[tag]), len(actual)))
		}
	}
	if verified == 0 || len(problems) == 0 {
		return ""
	}
	return "clients desired-state mismatch: " + strings.Join(problems, "; ")
}

// appendApplyLog appends one human readable line to state/apply.log.
func (a *Applier) appendApplyLog(revision int64, line string) {
	path := a.o.Paths.ApplyLog
	if path == "" {
		return
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return
	}
	if fi, err := os.Stat(path); err == nil && fi.Size() > DefaultApplyLogMaxBytes {
		_ = os.Rename(path, path+".1")
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return
	}
	defer func() { _ = f.Close() }()
	ts := time.Now().UTC().Format(time.RFC3339)
	_, _ = fmt.Fprintf(f, "%s revision=%d %s\n", ts, revision, sanitizeLine(line))
}

// xrayEnv returns the environment needed by xray for geodata lookups.
func (a *Applier) xrayEnv() []string {
	if a.o.AssetDir == "" {
		return nil
	}
	return []string{"XRAY_LOCATION_ASSET=" + a.o.AssetDir}
}

// fail fills the failure result.
func (a *Applier) fail(base Result, msg string, logs *strings.Builder) Result {
	base.Status = api.StatusFailed
	base.Error = msg
	base.LogTail = tailString(logs.String(), DefaultResultLogTailBytes)
	return base
}

// healthTargets describes what a health check must verify.
type healthTargets struct {
	Ports       []string
	APIAddr     string
	VerifyStats bool
}

// flexString accepts a JSON string or number where a string is expected.
type flexString string

// UnmarshalJSON implements json.Unmarshaler.
func (f *flexString) UnmarshalJSON(data []byte) error {
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "null" {
		*f = ""
		return nil
	}
	if len(trimmed) >= 2 && trimmed[0] == '"' {
		var s string
		if err := json.Unmarshal(data, &s); err != nil {
			return err
		}
		*f = flexString(s)
		return nil
	}
	*f = flexString(trimmed)
	return nil
}

// xrayConfig is the subset of the Xray configuration the agent inspects.
type xrayConfig struct {
	Inbounds []struct {
		Tag      string     `json:"tag"`
		Listen   flexString `json:"listen"`
		Port     int        `json:"port"`
		Protocol string     `json:"protocol"`
		Settings struct {
			Clients []struct {
				Email string `json:"email"`
			} `json:"clients"`
		} `json:"settings"`
	} `json:"inbounds"`
	API struct {
		Tag      string     `json:"tag"`
		Listen   flexString `json:"listen"`
		Services []string   `json:"services"`
	} `json:"api"`
}

// dialableProtocols are the inbound protocols whose TCP port can be probed.
var dialableProtocols = map[string]bool{
	"vless":       true,
	"vmess":       true,
	"trojan":      true,
	"socks":       true,
	"http":        true,
	"shadowsocks": true,
}

// parseHealthTargets extracts inbound ports and the stats API address.
func parseHealthTargets(raw []byte) healthTargets {
	var cfg xrayConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return healthTargets{}
	}
	var targets healthTargets
	for _, inbound := range cfg.Inbounds {
		if inbound.Port <= 0 || !dialableProtocols[strings.ToLower(inbound.Protocol)] {
			continue
		}
		listen := strings.TrimSpace(string(inbound.Listen))
		switch listen {
		case "", "0.0.0.0", "::", "[::]", "::0":
			listen = "127.0.0.1"
		}
		targets.Ports = append(targets.Ports, net.JoinHostPort(listen, fmt.Sprintf("%d", inbound.Port)))
	}
	listen := strings.TrimSpace(string(cfg.API.Listen))
	if listen != "" {
		for _, service := range cfg.API.Services {
			if strings.EqualFold(service, "StatsService") {
				targets.VerifyStats = true
			}
		}
		if targets.VerifyStats {
			host, port, err := net.SplitHostPort(listen)
			if err != nil || host == "" || port == "" {
				targets.VerifyStats = false
			} else if host == "0.0.0.0" || host == "::" {
				targets.APIAddr = net.JoinHostPort("127.0.0.1", port)
			} else {
				targets.APIAddr = listen
			}
		}
	}
	return targets
}

// parseClientEmails extracts the desired client emails per inbound tag.
func parseClientEmails(raw []byte) map[string][]string {
	var cfg xrayConfig
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil
	}
	out := map[string][]string{}
	for _, inbound := range cfg.Inbounds {
		if inbound.Tag == "" || len(inbound.Settings.Clients) == 0 {
			continue
		}
		emails := make([]string, 0, len(inbound.Settings.Clients))
		for _, client := range inbound.Settings.Clients {
			if client.Email != "" {
				emails = append(emails, client.Email)
			}
		}
		if len(emails) > 0 {
			out[inbound.Tag] = emails
		}
	}
	return out
}

// sameStringSet compares two string slices as sets.
func sameStringSet(a, b []string) bool {
	as := make([]string, len(a))
	copy(as, a)
	sort.Strings(as)
	bs := make([]string, len(b))
	copy(bs, b)
	sort.Strings(bs)
	if len(as) != len(bs) {
		return false
	}
	for i := range as {
		if as[i] != bs[i] {
			return false
		}
	}
	return true
}

// writeFileAtomic writes data to path via a temp file in the same directory.
func writeFileAtomic(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-"+filepath.Base(path)+"-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if err := tmp.Chmod(mode); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		return err
	}
	if dirHandle, err := os.Open(dir); err == nil {
		_ = dirHandle.Sync()
		_ = dirHandle.Close()
	}
	return nil
}

// restoreFile restores src over dst atomically, falling back to a copy.
func restoreFile(src, dst string) error {
	if err := os.Rename(src, dst); err == nil {
		return nil
	}
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return writeFileAtomic(dst, data, 0o600)
}

// canonicalJSON re-serializes JSON the way the panel compiler does:
// sorted object keys, compact separators and raw UTF-8 (ensure_ascii=False).
func canonicalJSON(raw []byte) ([]byte, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(buffer.Bytes(), []byte{'\n'}), nil
}

// canonicalSHA256 returns the SHA-256 of the canonical JSON representation.
func canonicalSHA256(raw []byte) (string, bool) {
	canonical, err := canonicalJSON(raw)
	if err != nil {
		return "", false
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), true
}

// sha256Hex returns the hex SHA-256 of data.
func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// tailString keeps at most max trailing bytes of s.
func tailString(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	return s[len(s)-max:]
}

// firstMeaningfulLine picks the most informative line of a command output.
func firstMeaningfulLine(output string) string {
	lines := strings.Split(output, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		lowered := strings.ToLower(line)
		if strings.Contains(lowered, "fail") || strings.Contains(lowered, "error") || strings.Contains(lowered, "invalid") {
			return line
		}
	}
	for i := len(lines) - 1; i >= 0; i-- {
		if line := strings.TrimSpace(lines[i]); line != "" {
			return line
		}
	}
	return "unknown error"
}

// sanitizeLine flattens and shortens a log line.
func sanitizeLine(line string) string {
	line = strings.ReplaceAll(line, "\n", " ")
	line = strings.ReplaceAll(line, "\r", "")
	if len(line) > 1000 {
		return line[:1000] + "..."
	}
	return line
}

// emptyOr returns value or fallback when value is empty.
func emptyOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
