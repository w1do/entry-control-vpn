package systemd

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// UnitOptions parametrises the generated unit files.
type UnitOptions struct {
	BaseDir    string
	AgentBin   string
	XrayBin    string
	XrayConfig string
	AssetDir   string
	UnitDir    string
	AgentUnit  string
	XrayUnit   string
}

// DefaultUnitOptions returns the canonical options for a base directory.
func DefaultUnitOptions(baseDir string) UnitOptions {
	return UnitOptions{
		BaseDir:    baseDir,
		AgentBin:   filepath.Join(baseDir, "bin", "vpn-agent"),
		XrayBin:    filepath.Join(baseDir, "bin", "xray"),
		XrayConfig: filepath.Join(baseDir, "etc", "xray.json"),
		AssetDir:   filepath.Join(baseDir, "etc"),
		UnitDir:    DefaultUnitDir,
		AgentUnit:  AgentUnitName,
		XrayUnit:   XrayUnitName,
	}
}

// normalized fills empty fields with defaults for the configured base dir.
func (o UnitOptions) normalized() UnitOptions {
	if o.UnitDir == "" {
		o.UnitDir = DefaultUnitDir
	}
	if o.AgentUnit == "" {
		o.AgentUnit = AgentUnitName
	}
	if o.XrayUnit == "" {
		o.XrayUnit = XrayUnitName
	}
	if o.AgentBin == "" {
		o.AgentBin = filepath.Join(o.BaseDir, "bin", "vpn-agent")
	}
	if o.XrayBin == "" {
		o.XrayBin = filepath.Join(o.BaseDir, "bin", "xray")
	}
	if o.XrayConfig == "" {
		o.XrayConfig = filepath.Join(o.BaseDir, "etc", "xray.json")
	}
	if o.AssetDir == "" {
		o.AssetDir = filepath.Join(o.BaseDir, "etc")
	}
	return o
}

// homeDirForBase derives $HOME from <home>/config/vpn (the agent contract).
func homeDirForBase(baseDir string) string {
	clean := filepath.Clean(baseDir)
	if filepath.Base(clean) == "vpn" && filepath.Base(filepath.Dir(clean)) == "config" {
		return filepath.Dir(filepath.Dir(clean))
	}
	return filepath.Dir(clean)
}

// RenderAgentUnit renders vpn-agent.service with the hardening required by
// specs/node-agent/spec.md.
func RenderAgentUnit(options UnitOptions) string {
	o := options.normalized()
	var b strings.Builder
	fmt.Fprintf(&b, "[Unit]\n")
	fmt.Fprintf(&b, "Description=VPN Control Panel node agent (vpn-agent)\n")
	fmt.Fprintf(&b, "After=network-online.target\n")
	fmt.Fprintf(&b, "Wants=network-online.target\n")
	fmt.Fprintf(&b, "StartLimitIntervalSec=0\n")
	fmt.Fprintf(&b, "\n")
	fmt.Fprintf(&b, "[Service]\n")
	fmt.Fprintf(&b, "Type=simple\n")
	home := homeDirForBase(o.BaseDir)
	fmt.Fprintf(&b, "Environment=HOME=%s\n", EscapeExecArg(home))
	fmt.Fprintf(&b, "ExecStart=%s serve --base %s\n", EscapeExecArg(o.AgentBin), EscapeExecArg(o.BaseDir))
	fmt.Fprintf(&b, "Restart=always\n")
	fmt.Fprintf(&b, "RestartSec=5s\n")
	fmt.Fprintf(&b, "TimeoutStopSec=20s\n")
	fmt.Fprintf(&b, "# Files created by the agent are only accessible to root.\n")
	fmt.Fprintf(&b, "UMask=0077\n")
	fmt.Fprintf(&b, "NoNewPrivileges=yes\n")
	fmt.Fprintf(&b, "ProtectSystem=strict\n")
	fmt.Fprintf(&b, "# The base directory lives under the root home directory.\n")
	fmt.Fprintf(&b, "ProtectHome=no\n")
	fmt.Fprintf(&b, "ReadWritePaths=%s\n", EscapeExecArg(o.BaseDir))
	fmt.Fprintf(&b, "PrivateTmp=yes\n")
	fmt.Fprintf(&b, "ProtectKernelTunables=yes\n")
	fmt.Fprintf(&b, "ProtectControlGroups=yes\n")
	fmt.Fprintf(&b, "RestrictRealtime=yes\n")
	fmt.Fprintf(&b, "RestrictSUIDSGID=yes\n")
	fmt.Fprintf(&b, "LockPersonality=yes\n")
	fmt.Fprintf(&b, "# systemctl talks to PID 1 over a unix socket; nft/ufw use netlink.\n")
	fmt.Fprintf(&b, "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK\n")
	fmt.Fprintf(&b, "\n")
	fmt.Fprintf(&b, "[Install]\n")
	fmt.Fprintf(&b, "WantedBy=multi-user.target\n")
	return b.String()
}

// RenderXrayUnit renders xray.service for the configured base directory.
func RenderXrayUnit(options UnitOptions) string {
	o := options.normalized()
	var b strings.Builder
	fmt.Fprintf(&b, "[Unit]\n")
	fmt.Fprintf(&b, "Description=Xray-core VPN node\n")
	fmt.Fprintf(&b, "After=network-online.target\n")
	fmt.Fprintf(&b, "Wants=network-online.target\n")
	fmt.Fprintf(&b, "StartLimitIntervalSec=0\n")
	fmt.Fprintf(&b, "\n")
	fmt.Fprintf(&b, "[Service]\n")
	fmt.Fprintf(&b, "Type=simple\n")
	fmt.Fprintf(&b, "ExecStart=%s run -c %s\n", EscapeExecArg(o.XrayBin), EscapeExecArg(o.XrayConfig))
	fmt.Fprintf(&b, "Environment=XRAY_LOCATION_ASSET=%s\n", o.AssetDir)
	fmt.Fprintf(&b, "Restart=on-failure\n")
	fmt.Fprintf(&b, "RestartSec=2s\n")
	fmt.Fprintf(&b, "LimitNOFILE=65535\n")
	fmt.Fprintf(&b, "UMask=0077\n")
	fmt.Fprintf(&b, "NoNewPrivileges=yes\n")
	fmt.Fprintf(&b, "ProtectSystem=strict\n")
	fmt.Fprintf(&b, "ProtectHome=no\n")
	fmt.Fprintf(&b, "ReadWritePaths=%s\n", EscapeExecArg(o.BaseDir))
	fmt.Fprintf(&b, "PrivateTmp=yes\n")
	fmt.Fprintf(&b, "ProtectKernelTunables=yes\n")
	fmt.Fprintf(&b, "ProtectKernelModules=yes\n")
	fmt.Fprintf(&b, "ProtectControlGroups=yes\n")
	fmt.Fprintf(&b, "RestrictRealtime=yes\n")
	fmt.Fprintf(&b, "RestrictSUIDSGID=yes\n")
	fmt.Fprintf(&b, "LockPersonality=yes\n")
	fmt.Fprintf(&b, "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n")
	fmt.Fprintf(&b, "\n")
	fmt.Fprintf(&b, "[Install]\n")
	fmt.Fprintf(&b, "WantedBy=multi-user.target\n")
	return b.String()
}

// EscapeExecArg quotes a path for a systemd ExecStart/ReadWritePaths value.
// Absolute paths without special characters are returned unchanged.
func EscapeExecArg(value string) string {
	if value == "" {
		return ""
	}
	needsQuotes := strings.ContainsAny(value, " \t\n\"")
	escaped := strings.ReplaceAll(value, "%", "%%")
	escaped = strings.ReplaceAll(escaped, "\\", "\\\\")
	escaped = strings.ReplaceAll(escaped, "\"", "\\\"")
	if needsQuotes || escaped != value {
		return "\"" + escaped + "\""
	}
	return escaped
}

// UnitPath returns the absolute path of a unit file.
func UnitPath(unitDir, name string) string {
	if unitDir == "" {
		unitDir = DefaultUnitDir
	}
	return filepath.Join(unitDir, name)
}

// WriteUnitFile atomically installs a unit file with mode 0644.
func WriteUnitFile(unitDir, name, content string) (string, error) {
	path := UnitPath(unitDir, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", fmt.Errorf("systemd: create unit dir: %w", err)
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+name+".tmp-*")
	if err != nil {
		return "", fmt.Errorf("systemd: create temp unit: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if err := tmp.Chmod(0o644); err != nil {
		_ = tmp.Close()
		return "", fmt.Errorf("systemd: chmod temp unit: %w", err)
	}
	if _, err := tmp.WriteString(content); err != nil {
		_ = tmp.Close()
		return "", fmt.Errorf("systemd: write temp unit: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return "", fmt.Errorf("systemd: fsync temp unit: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return "", fmt.Errorf("systemd: close temp unit: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return "", fmt.Errorf("systemd: install unit %s: %w", path, err)
	}
	return path, nil
}

// RemoveUnitFile removes a unit file, ignoring a missing file.
func RemoveUnitFile(unitDir, name string) error {
	path := UnitPath(unitDir, name)
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("systemd: remove unit %s: %w", path, err)
	}
	return nil
}
