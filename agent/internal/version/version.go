// Package version holds build metadata for the vpn-agent binary.
package version

// Name is the canonical program name used in logs, the user agent and payloads.
const Name = "vpn-agent"

// Version is the agent version. It is overridden at build time with
// -ldflags "-X vpn-agent/internal/version.Version=<ver>".
var Version = "0.1.0"

// UserAgent returns the HTTP user agent describing this agent build.
func UserAgent() string { return Name + "/" + Version }
