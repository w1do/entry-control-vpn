// Package api implements the outbound HTTPS protocol between the node agent
// and the control panel (/agent/v1). The agent never opens listening ports.
package api

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Endpoint paths defined by docs/INTERFACES.md §2.4.
const (
	PathRegister      = "/agent/v1/register"
	PathHeartbeat     = "/agent/v1/heartbeat"
	PathConfig        = "/agent/v1/config"
	PathApplyResult   = "/agent/v1/apply-result"
	PathMetrics       = "/agent/v1/metrics"
	PathDiagnose      = "/agent/v1/diagnose"
	PathUninstall     = "/agent/v1/uninstall"
	PathCommandResult = "/agent/v1/command-result"
	PathArtifacts     = "/agent/v1/artifacts"
)

// ForceRevision is sent as the revision query parameter when the panel asks
// the agent to reapply the current configuration (reapply command). The panel
// returns the current revision whenever the requested one differs, so a
// sentinel that can never match forces a fresh download.
const ForceRevision int64 = -1

// Apply result statuses.
const (
	StatusApplied = "applied"
	StatusFailed  = "failed"
)

// Command statuses reported through /agent/v1/command-result.
const (
	CommandDone     = "done"
	CommandError    = "error"
	UninstallStatus = "done"
)

// Command types accepted from the panel.
const (
	CommandReapply        = "reapply"
	CommandRestartService = "restart_services"
	CommandDiagnose       = "diagnose"
	CommandUninstall      = "uninstall"
	CommandUpdateAgent    = "update_agent"
)

// Revision is a panel configuration revision. The panel may send it as a JSON
// number or as a string ("pending", "12"); both forms are accepted.
type Revision int64

// UnmarshalJSON accepts numbers, numeric strings and the "pending" marker.
func (r *Revision) UnmarshalJSON(data []byte) error {
	s := strings.TrimSpace(string(data))
	if s == "null" {
		*r = 0
		return nil
	}
	if len(s) >= 2 && s[0] == '"' && s[len(s)-1] == '"' {
		var str string
		if err := json.Unmarshal(data, &str); err != nil {
			return fmt.Errorf("api: invalid revision %s: %w", s, err)
		}
		str = strings.TrimSpace(str)
		if str == "" || str == "pending" || str == "none" {
			*r = 0
			return nil
		}
		n, err := strconv.ParseInt(str, 10, 64)
		if err != nil {
			return fmt.Errorf("api: invalid revision %q: %w", str, err)
		}
		*r = Revision(n)
		return nil
	}
	var n int64
	if err := json.Unmarshal(data, &n); err != nil {
		return fmt.Errorf("api: invalid revision %s: %w", s, err)
	}
	*r = Revision(n)
	return nil
}

// MarshalJSON always emits a JSON number.
func (r Revision) MarshalJSON() ([]byte, error) {
	return []byte(strconv.FormatInt(int64(r), 10)), nil
}

// RegisterRequest is POST /agent/v1/register.
type RegisterRequest struct {
	AgentVersion string   `json:"agent_version"`
	XrayVersion  string   `json:"xray_version"`
	Hostname     string   `json:"hostname"`
	OS           string   `json:"os"`
	Arch         string   `json:"arch"`
	Capabilities []string `json:"capabilities"`
}

// RegisterResponse is the panel answer to a registration.
type RegisterResponse struct {
	ServerID int64    `json:"server_id"`
	Revision Revision `json:"revision"`
}

// XrayStatus describes the Xray core on the node.
type XrayStatus struct {
	Active     bool   `json:"active"`
	UptimeS    int64  `json:"uptime_s"`
	Version    string `json:"version"`
	PID        int    `json:"pid,omitempty"`
	Goroutines int    `json:"goroutines,omitempty"`
}

// HeartbeatRequest is POST /agent/v1/heartbeat.
type HeartbeatRequest struct {
	Revision       int64             `json:"revision"`
	Xray           XrayStatus        `json:"xray"`
	ConfigSHA256   string            `json:"config_sha256,omitempty"`
	Units          map[string]string `json:"units"`
	DegradedReason string            `json:"degraded_reason,omitempty"`
}

// Command is one panel command carried in a heartbeat/metrics response.
type Command struct {
	ID      string          `json:"id"`
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload,omitempty"`
}

// HeartbeatResponse is the panel answer to a heartbeat.
type HeartbeatResponse struct {
	Revision     Revision  `json:"revision"`
	ApplyPending bool      `json:"apply_pending"`
	Commands     []Command `json:"commands,omitempty"`
}

// ConfigResponse is a 200 answer to GET /agent/v1/config.
type ConfigResponse struct {
	Revision Revision        `json:"revision"`
	Config   json.RawMessage `json:"config"`
	SHA256   string          `json:"sha256"`
}

// ApplyResult is POST /agent/v1/apply-result.
type ApplyResult struct {
	Revision int64  `json:"revision"`
	Status   string `json:"status"`
	Error    string `json:"error,omitempty"`
	LogTail  string `json:"log_tail,omitempty"`
	SHA256   string `json:"sha256,omitempty"`
}

// Traffic is a pair of byte counters.
type Traffic struct {
	Up   int64 `json:"up"`
	Down int64 `json:"down"`
}

// Diagnostics carries inbound/outbound counter deltas (diagnostics only;
// client quotas are always computed from user>>> counters).
type Diagnostics struct {
	Inbound  map[string]Traffic `json:"inbound,omitempty"`
	Outbound map[string]Traffic `json:"outbound,omitempty"`
}

// ClientMetrics is the per-client part of a metrics batch.
type ClientMetrics struct {
	Email  string   `json:"email"`
	Up     int64    `json:"up"`
	Down   int64    `json:"down"`
	Online bool     `json:"online"`
	IPs    []string `json:"ips,omitempty"`
}

// Connections is the connection statistics part of a metrics batch.
type Connections struct {
	Accepted15s int64 `json:"accepted_15s"`
	Blocked15s  int64 `json:"blocked_15s"`
	Online      int   `json:"online"`
}

// Errors is the error statistics part of a metrics batch.
type Errors struct {
	Rejected15s  int64 `json:"rejected_15s"`
	XrayError15s int64 `json:"xray_error_15s"`
}

// MetricsBatch is one POST /agent/v1/metrics body. Timestamps are UTC.
type MetricsBatch struct {
	TS           time.Time       `json:"ts"`
	Revision     int64           `json:"revision"`
	AgentVersion string          `json:"agent_version,omitempty"`
	Xray         XrayStatus      `json:"xray"`
	TrafficDelta Traffic         `json:"traffic_delta"`
	Clients      []ClientMetrics `json:"clients"`
	Connections  Connections     `json:"connections"`
	Errors       Errors          `json:"errors"`
	Replay       bool            `json:"replay,omitempty"`
	Diagnostics  *Diagnostics    `json:"diagnostics,omitempty"`
}

// MetricsResponse is the panel answer to a metrics batch.
type MetricsResponse struct {
	OK       bool      `json:"ok"`
	Accepted bool      `json:"accepted"`
	Commands []Command `json:"commands,omitempty"`
}

// CommandResult is POST /agent/v1/command-result.
type CommandResult struct {
	CommandID string `json:"command_id"`
	Status    string `json:"status"`
	Output    string `json:"output,omitempty"`
}

// DiagnoseRequest is POST /agent/v1/diagnose.
type DiagnoseRequest struct {
	Report json.RawMessage `json:"report"`
}

// UninstallRequest is POST /agent/v1/uninstall.
type UninstallRequest struct {
	Status string `json:"status"`
}

// Artifact is a downloaded agent binary with its verified checksum.
type Artifact struct {
	Name     string
	Data     []byte
	Checksum string
}

// ParseXrayEmails extracts user emails from the JSON produced by
// "xray api inbounduser". The exact shape may evolve, so all "email" string
// fields are collected recursively.
func ParseXrayEmails(data []byte) []string { return collectStringsForKey(data, "email") }

// collectStringsForKey walks arbitrary JSON and returns values of a given key.
func collectStringsForKey(data []byte, key string) []string {
	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		return nil
	}
	seen := map[string]struct{}{}
	var out []string
	var walk func(any)
	walk = func(node any) {
		switch n := node.(type) {
		case map[string]any:
			for k, val := range n {
				if k == key {
					if s, ok := val.(string); ok && s != "" {
						if _, dup := seen[s]; !dup {
							seen[s] = struct{}{}
							out = append(out, s)
						}
					}
				}
				walk(val)
			}
		case []any:
			for _, item := range n {
				walk(item)
			}
		}
	}
	walk(v)
	return out
}
