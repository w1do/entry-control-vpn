// Package config loads and validates the node agent configuration.
//
// The single configuration file lives at <base>/etc/agent.json and contains
// the panel URL, the per-node bearer token and the tuning knobs of the agent.
// Everything else is derived from base_dir according to docs/INTERFACES.md §4.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Defaults for optional configuration fields.
const (
	DefaultHeartbeatIntervalS = 30
	DefaultConfigPollS        = 10
	DefaultMetricsIntervalS   = 15

	DefaultRequestTimeoutS       = 15
	DefaultHealthTimeoutS        = 15
	DefaultApplyTestTimeoutS     = 60
	DefaultApplyRestartTimeoutS  = 60
	DefaultMetricsBufferTTLHours = 24
	DefaultReplayBatchLimit      = 240
	DefaultOnlineWindowS         = 120
	DefaultRetryMaxBackoffS      = 60
	DefaultAuthBackoffS          = 300
	DefaultHTTPMaxAttempts       = 3
	DefaultApplyLogMaxBytes      = 256 << 10
	DefaultLogReadMaxBytes       = 4 << 20
	DefaultDiagnoseLogLines      = 200
	DefaultMetricsBufferMaxBytes = 32 << 20
	DefaultStateSchema           = 1
)

// EnvBaseDir is the environment variable that can point the agent at its base
// directory when --base is not given.
const EnvBaseDir = "VPN_AGENT_BASE"

// Config is the on-disk agent configuration (<base>/etc/agent.json).
type Config struct {
	PanelURL string `json:"panel_url"`
	Token    string `json:"token"`
	ServerID int64  `json:"server_id"`
	BaseDir  string `json:"base_dir"`

	HeartbeatIntervalS int `json:"heartbeat_interval_s,omitempty"`
	ConfigPollS        int `json:"config_poll_s,omitempty"`
	MetricsIntervalS   int `json:"metrics_interval_s,omitempty"`

	RequestTimeoutS       int `json:"request_timeout_s,omitempty"`
	HealthTimeoutS        int `json:"health_timeout_s,omitempty"`
	ApplyTestTimeoutS     int `json:"apply_test_timeout_s,omitempty"`
	ApplyRestartTimeoutS  int `json:"apply_restart_timeout_s,omitempty"`
	MetricsBufferTTLHours int `json:"metrics_buffer_ttl_hours,omitempty"`
	MetricsBufferMaxBytes int `json:"metrics_buffer_max_bytes,omitempty"`
	ReplayBatchLimit      int `json:"replay_batch_limit,omitempty"`
	OnlineWindowS         int `json:"online_window_s,omitempty"`
	RetryMaxBackoffS      int `json:"retry_max_backoff_s,omitempty"`
	AuthBackoffS          int `json:"auth_backoff_s,omitempty"`
	HTTPMaxAttempts       int `json:"http_max_attempts,omitempty"`

	XrayAPIAddr  string `json:"xray_api_addr,omitempty"`
	XrayUnit     string `json:"xray_unit,omitempty"`
	AgentUnit    string `json:"agent_unit,omitempty"`
	XrayBinary   string `json:"xray_binary,omitempty"`
	SystemctlBin string `json:"systemctl_bin,omitempty"`
	NftBin       string `json:"nft_bin,omitempty"`
	UfwBin       string `json:"ufw_bin,omitempty"`
	AssetDir     string `json:"xray_asset_dir,omitempty"`
	UnitDir      string `json:"unit_dir,omitempty"`
	ArtifactName string `json:"artifact_name,omitempty"`

	TLSSkipVerify   bool   `json:"tls_insecure_skip_verify,omitempty"`
	SendDiagnostics bool   `json:"send_diagnostics_metrics,omitempty"`
	LogLevel        string `json:"log_level,omitempty"`

	// Warnings collects non fatal problems found while loading the config
	// (for example a token file readable by group/others). Not serialized.
	Warnings []string `json:"-"`

	paths Paths
}

// Paths is the complete layout of the node state root. It is derived from the
// configured base directory and must match docs/INTERFACES.md §4.
type Paths struct {
	BaseDir       string
	BinDir        string
	EtcDir        string
	LogDir        string
	StateDir      string
	ShareDir      string
	XrayBin       string
	AgentBin      string
	XrayConfig    string
	XrayConfigNew string
	XrayBackup    string
	AgentConfig   string
	AccessLog     string
	ErrorLog      string
	ApplyLog      string
	StateFile     string
	MetricsBuffer string
	UnitDir       string
}

// New returns a Config pre-filled with defaults for the given base directory.
// It is used by Load and by tests that build a configuration programmatically.
func New(baseDir string) *Config {
	c := &Config{BaseDir: baseDir}
	c.applyDefaults()
	c.paths = derivePaths(c)
	return c
}

// Load reads <baseDir>/etc/agent.json, applies defaults and validates it.
func Load(baseDir string) (*Config, error) {
	base, err := filepath.Abs(baseDir)
	if err != nil {
		return nil, fmt.Errorf("config: resolve base dir: %w", err)
	}
	path := filepath.Join(base, "etc", "agent.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("config: read %s: %w", path, err)
	}
	c := New(base)
	if err := json.Unmarshal(raw, c); err != nil {
		return nil, fmt.Errorf("config: parse %s: %w", path, err)
	}
	if err := c.Finalize(); err != nil {
		return nil, err
	}
	if fi, err := os.Stat(path); err == nil {
		if mode := fi.Mode().Perm(); mode&0o077 != 0 {
			c.Warnings = append(c.Warnings, fmt.Sprintf(
				"%s has mode %04o; expected 0600 (the file contains the agent token)", path, mode))
		}
	}
	return c, nil
}

// Finalize expands and validates the configuration and recomputes Paths.
// It is safe to call on a Config produced by New.
func (c *Config) Finalize() error {
	if strings.TrimSpace(c.BaseDir) == "" {
		return errors.New("config: base_dir is empty")
	}
	base, err := expandPath(c.BaseDir)
	if err != nil {
		return fmt.Errorf("config: base_dir: %w", err)
	}
	if !filepath.IsAbs(base) {
		return fmt.Errorf("config: base_dir %q must be absolute after expansion", c.BaseDir)
	}
	c.BaseDir = filepath.Clean(base)
	c.applyDefaults()
	c.paths = derivePaths(c)

	if strings.TrimSpace(c.PanelURL) == "" {
		return errors.New("config: panel_url is required")
	}
	if err := validatePanelURL(c.PanelURL); err != nil {
		return err
	}
	if strings.TrimSpace(c.Token) == "" {
		return errors.New("config: token is required")
	}
	if c.ServerID < 0 {
		return fmt.Errorf("config: server_id must be >= 0, got %d", c.ServerID)
	}
	if err := validateIntervals(c); err != nil {
		return err
	}
	for name, unit := range map[string]string{"xray_unit": c.XrayUnit, "agent_unit": c.AgentUnit} {
		if err := validateUnitName(unit); err != nil {
			return fmt.Errorf("config: %s: %w", name, err)
		}
	}
	if _, _, err := splitHostPort(c.XrayAPIAddr); err != nil {
		return fmt.Errorf("config: xray_api_addr: %w", err)
	}
	if !filepath.IsAbs(c.UnitDir) {
		return fmt.Errorf("config: unit_dir %q must be absolute", c.UnitDir)
	}
	if strings.TrimSpace(c.XrayBinary) == "" {
		return errors.New("config: xray_binary is empty")
	}
	return nil
}

// applyDefaults fills zero valued optional fields with their defaults.
func (c *Config) applyDefaults() {
	if c.HeartbeatIntervalS == 0 {
		c.HeartbeatIntervalS = DefaultHeartbeatIntervalS
	}
	if c.ConfigPollS == 0 {
		c.ConfigPollS = DefaultConfigPollS
	}
	if c.MetricsIntervalS == 0 {
		c.MetricsIntervalS = DefaultMetricsIntervalS
	}
	if c.RequestTimeoutS == 0 {
		c.RequestTimeoutS = DefaultRequestTimeoutS
	}
	if c.HealthTimeoutS == 0 {
		c.HealthTimeoutS = DefaultHealthTimeoutS
	}
	if c.ApplyTestTimeoutS == 0 {
		c.ApplyTestTimeoutS = DefaultApplyTestTimeoutS
	}
	if c.ApplyRestartTimeoutS == 0 {
		c.ApplyRestartTimeoutS = DefaultApplyRestartTimeoutS
	}
	if c.MetricsBufferTTLHours == 0 {
		c.MetricsBufferTTLHours = DefaultMetricsBufferTTLHours
	}
	if c.MetricsBufferMaxBytes == 0 {
		c.MetricsBufferMaxBytes = DefaultMetricsBufferMaxBytes
	}
	if c.ReplayBatchLimit == 0 {
		c.ReplayBatchLimit = DefaultReplayBatchLimit
	}
	if c.OnlineWindowS == 0 {
		c.OnlineWindowS = DefaultOnlineWindowS
	}
	if c.RetryMaxBackoffS == 0 {
		c.RetryMaxBackoffS = DefaultRetryMaxBackoffS
	}
	if c.AuthBackoffS == 0 {
		c.AuthBackoffS = DefaultAuthBackoffS
	}
	if c.HTTPMaxAttempts == 0 {
		c.HTTPMaxAttempts = DefaultHTTPMaxAttempts
	}
	if strings.TrimSpace(c.XrayAPIAddr) == "" {
		c.XrayAPIAddr = "127.0.0.1:10085"
	}
	if strings.TrimSpace(c.XrayUnit) == "" {
		c.XrayUnit = "xray.service"
	}
	if strings.TrimSpace(c.AgentUnit) == "" {
		c.AgentUnit = "vpn-agent.service"
	}
	if strings.TrimSpace(c.SystemctlBin) == "" {
		c.SystemctlBin = "systemctl"
	}
	if strings.TrimSpace(c.NftBin) == "" {
		c.NftBin = "nft"
	}
	if strings.TrimSpace(c.UfwBin) == "" {
		c.UfwBin = "ufw"
	}
	if strings.TrimSpace(c.UnitDir) == "" {
		c.UnitDir = "/etc/systemd/system"
	}
	if strings.TrimSpace(c.LogLevel) == "" {
		c.LogLevel = "info"
	}
	if strings.TrimSpace(c.XrayBinary) == "" && c.BaseDir != "" {
		c.XrayBinary = filepath.Join(c.BaseDir, "bin", "xray")
	}
	if strings.TrimSpace(c.AssetDir) == "" && c.BaseDir != "" {
		// The panel provisioner uploads geoip.dat/geosite.dat into <base>/etc
		// and starts xray with XRAY_LOCATION_ASSET=<base>/etc.
		c.AssetDir = filepath.Join(c.BaseDir, "etc")
	}
}

// derivePaths computes the state layout for the configured base directory.
func derivePaths(c *Config) Paths {
	base := c.BaseDir
	p := Paths{
		BaseDir:       base,
		BinDir:        filepath.Join(base, "bin"),
		EtcDir:        filepath.Join(base, "etc"),
		LogDir:        filepath.Join(base, "log"),
		StateDir:      filepath.Join(base, "state"),
		ShareDir:      filepath.Join(base, "share"),
		XrayBin:       c.XrayBinary,
		AgentBin:      filepath.Join(base, "bin", "vpn-agent"),
		XrayConfig:    filepath.Join(base, "etc", "xray.json"),
		XrayConfigNew: filepath.Join(base, "etc", "xray.json.new"),
		XrayBackup:    filepath.Join(base, "etc", "xray.json.bak"),
		AgentConfig:   filepath.Join(base, "etc", "agent.json"),
		AccessLog:     filepath.Join(base, "log", "access.log"),
		ErrorLog:      filepath.Join(base, "log", "xray-error.log"),
		ApplyLog:      filepath.Join(base, "state", "apply.log"),
		StateFile:     filepath.Join(base, "state", "state.json"),
		MetricsBuffer: filepath.Join(base, "state", "metrics-buffer.jsonl"),
		UnitDir:       c.UnitDir,
	}
	if c.AssetDir != "" {
		p.ShareDir = c.AssetDir
	}
	return p
}

// Paths returns the state layout derived from the configuration.
func (c *Config) Paths() Paths { return c.paths }

// EnsureDirs creates the base directory layout and removes world/group access
// from directories that hold secrets.
func (c *Config) EnsureDirs() error {
	p := c.paths
	dirs := []struct {
		path string
		mode os.FileMode
	}{
		{p.BaseDir, 0o700},
		{p.BinDir, 0o755},
		{p.LogDir, 0o750},
		{p.EtcDir, 0o700},
		{p.StateDir, 0o700},
	}
	for _, d := range dirs {
		if err := os.MkdirAll(d.path, d.mode); err != nil {
			return fmt.Errorf("config: create %s: %w", d.path, err)
		}
		if err := os.Chmod(d.path, d.mode); err != nil {
			return fmt.Errorf("config: chmod %s: %w", d.path, err)
		}
	}
	return nil
}

// HeartbeatInterval returns the heartbeat period.
func (c *Config) HeartbeatInterval() time.Duration {
	return time.Duration(c.HeartbeatIntervalS) * time.Second
}

// ConfigPollInterval returns the configuration poll period.
func (c *Config) ConfigPollInterval() time.Duration {
	return time.Duration(c.ConfigPollS) * time.Second
}

// MetricsInterval returns the metrics collection period.
func (c *Config) MetricsInterval() time.Duration {
	return time.Duration(c.MetricsIntervalS) * time.Second
}

// RequestTimeout returns the per-HTTP-request timeout.
func (c *Config) RequestTimeout() time.Duration {
	return time.Duration(c.RequestTimeoutS) * time.Second
}

// HealthTimeout returns how long the agent waits for Xray to become healthy
// after a restart before rolling back.
func (c *Config) HealthTimeout() time.Duration {
	return time.Duration(c.HealthTimeoutS) * time.Second
}

// ApplyTestTimeout returns the xray -test timeout.
func (c *Config) ApplyTestTimeout() time.Duration {
	return time.Duration(c.ApplyTestTimeoutS) * time.Second
}

// ApplyRestartTimeout returns the systemctl restart timeout.
func (c *Config) ApplyRestartTimeout() time.Duration {
	return time.Duration(c.ApplyRestartTimeoutS) * time.Second
}

// MetricsBufferTTL returns how long metrics may stay buffered while the panel
// is unreachable.
func (c *Config) MetricsBufferTTL() time.Duration {
	return time.Duration(c.MetricsBufferTTLHours) * time.Hour
}

// OnlineWindow returns the window used for the last_seen online fallback.
func (c *Config) OnlineWindow() time.Duration {
	return time.Duration(c.OnlineWindowS) * time.Second
}

// RetryMaxBackoff returns the maximum backoff between retries.
func (c *Config) RetryMaxBackoff() time.Duration {
	return time.Duration(c.RetryMaxBackoffS) * time.Second
}

// AuthBackoff returns how long the agent stays quiet after a 401/403.
func (c *Config) AuthBackoff() time.Duration {
	return time.Duration(c.AuthBackoffS) * time.Second
}

// DiagnoseLogLines returns how many log lines a diagnose report includes.
func (c *Config) DiagnoseLogLines() int { return DefaultDiagnoseLogLines }

// EffectiveArtifactName returns the artifact name requested from
// /agent/v1/artifacts for self-update.
func (c *Config) EffectiveArtifactName() string {
	if name := strings.TrimSpace(c.ArtifactName); name != "" {
		return name
	}
	return fmt.Sprintf("vpn-agent-%s-%s", runtime.GOOS, runtime.GOARCH)
}

// DefaultBaseDir returns the default base directory:
// $VPN_AGENT_BASE when set, else /root/config/vpn for root and
// ~/config/vpn for unprivileged users.
func DefaultBaseDir() (string, error) {
	if env := strings.TrimSpace(os.Getenv(EnvBaseDir)); env != "" {
		return filepath.Abs(env)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("config: determine home directory: %w", err)
	}
	return defaultBaseDir(home, os.Geteuid() == 0), nil
}

// ResolveBaseDir resolves the base directory from the --base flag, the
// environment and the process identity, in that order.
func ResolveBaseDir(flagValue string) (string, error) {
	if strings.TrimSpace(flagValue) != "" {
		return filepath.Abs(flagValue)
	}
	return DefaultBaseDir()
}

// defaultBaseDir is the pure helper behind DefaultBaseDir.
func defaultBaseDir(home string, asRoot bool) string {
	if asRoot {
		return filepath.Join("/root", "config", "vpn")
	}
	return filepath.Join(home, "config", "vpn")
}

// expandPath expands a leading "~/" using the current home directory.
func expandPath(p string) (string, error) {
	p = strings.TrimSpace(p)
	if p == "~" || strings.HasPrefix(p, "~/") {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		if p == "~" {
			return home, nil
		}
		return filepath.Join(home, p[2:]), nil
	}
	return p, nil
}

// validatePanelURL checks that panel_url is an absolute http(s) URL.
func validatePanelURL(raw string) error {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return fmt.Errorf("config: panel_url: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("config: panel_url must use http or https, got %q", u.Scheme)
	}
	if u.Host == "" {
		return errors.New("config: panel_url must include a host")
	}
	if u.RawQuery != "" || u.Fragment != "" {
		return errors.New("config: panel_url must not contain a query or fragment")
	}
	return nil
}

// validateIntervals checks that every interval is within sane bounds.
func validateIntervals(c *Config) error {
	checks := []struct {
		name     string
		value    int
		min, max int
	}{
		{"heartbeat_interval_s", c.HeartbeatIntervalS, 5, 3600},
		{"config_poll_s", c.ConfigPollS, 1, 3600},
		{"metrics_interval_s", c.MetricsIntervalS, 5, 3600},
		{"request_timeout_s", c.RequestTimeoutS, 1, 300},
		{"health_timeout_s", c.HealthTimeoutS, 1, 600},
		{"apply_test_timeout_s", c.ApplyTestTimeoutS, 1, 600},
		{"apply_restart_timeout_s", c.ApplyRestartTimeoutS, 1, 600},
		{"metrics_buffer_ttl_hours", c.MetricsBufferTTLHours, 1, 168},
		{"replay_batch_limit", c.ReplayBatchLimit, 1, 10000},
		{"online_window_s", c.OnlineWindowS, 10, 3600},
		{"retry_max_backoff_s", c.RetryMaxBackoffS, 5, 600},
		{"auth_backoff_s", c.AuthBackoffS, 5, 3600},
		{"http_max_attempts", c.HTTPMaxAttempts, 1, 10},
	}
	for _, ck := range checks {
		if ck.value < ck.min || ck.value > ck.max {
			return fmt.Errorf("config: %s must be between %d and %d, got %d", ck.name, ck.min, ck.max, ck.value)
		}
	}
	return nil
}

// validateUnitName checks that a systemd unit name is plausible and safe.
func validateUnitName(name string) error {
	if name == "" {
		return errors.New("unit name is empty")
	}
	if strings.ContainsAny(name, "/\\ \t\n") {
		return fmt.Errorf("unit name %q contains forbidden characters", name)
	}
	return nil
}

// splitHostPort validates host:port without importing net twice in callers.
func splitHostPort(addr string) (string, int, error) {
	host, portStr, err := netSplitHostPort(addr)
	if err != nil {
		return "", 0, err
	}
	port, err := strconv.Atoi(portStr)
	if err != nil || port <= 0 || port > 65535 {
		return "", 0, fmt.Errorf("invalid port in %q", addr)
	}
	if host == "" {
		return "", 0, fmt.Errorf("missing host in %q", addr)
	}
	return host, port, nil
}
