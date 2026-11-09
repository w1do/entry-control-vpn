package metrics

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/netip"
	"sort"
	"strconv"
	"strings"
)

// Scope prefixes of Xray statistic names.
const (
	ScopeUser     = "user"
	ScopeInbound  = "inbound"
	ScopeOutbound = "outbound"
)

// ParseStatsQuery parses the JSON printed by "xray api statsquery".
//
// The CLI prints {"stat":[{"name":"...","value":123}]} and omits "value" for
// zero counters. Empty statistics are printed as {}. Values may be numbers or
// strings depending on the Xray build, so both are accepted.
func ParseStatsQuery(data []byte) (map[string]int64, error) {
	out := map[string]int64{}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return out, nil
	}
	dec := json.NewDecoder(bytes.NewReader(trimmed))
	dec.UseNumber()
	var root any
	if err := dec.Decode(&root); err != nil {
		return nil, fmt.Errorf("metrics: parse statsquery output: %w", err)
	}
	walkStatNodes(root, out)
	return out, nil
}

// walkStatNodes collects name/value pairs from any response shape.
func walkStatNodes(node any, out map[string]int64) {
	switch n := node.(type) {
	case map[string]any:
		if name, ok := n["name"].(string); ok && name != "" {
			out[name] = statValue(n["value"])
			return
		}
		for _, v := range n {
			walkStatNodes(v, out)
		}
	case []any:
		for _, item := range n {
			walkStatNodes(item, out)
		}
	}
}

// statValue converts a statistic value to int64 without losing precision.
func statValue(value any) int64 {
	switch n := value.(type) {
	case json.Number:
		if i, err := n.Int64(); err == nil {
			return i
		}
		if f, err := n.Float64(); err == nil {
			return int64(f)
		}
	case string:
		text := strings.TrimSpace(n)
		if i, err := strconv.ParseInt(text, 10, 64); err == nil {
			return i
		}
		if f, err := strconv.ParseFloat(text, 64); err == nil {
			return int64(f)
		}
	case float64:
		return int64(n)
	}
	return 0
}

// StatsName is a parsed "scope>>>tag>>>traffic>>>direction" statistic name.
type StatsName struct {
	Scope     string
	Tag       string
	Direction string
}

// ParseStatsName splits a traffic statistic name. Only traffic counters are
// accepted; online counters are handled separately.
func ParseStatsName(name string) (StatsName, bool) {
	parts := strings.Split(name, ">>>")
	if len(parts) < 4 {
		return StatsName{}, false
	}
	direction := parts[len(parts)-1]
	if direction != "uplink" && direction != "downlink" {
		return StatsName{}, false
	}
	if parts[len(parts)-2] != "traffic" {
		return StatsName{}, false
	}
	return StatsName{Scope: parts[0], Tag: parts[1], Direction: direction}, true
}

// Online is the set of online clients and the IP addresses seen by the Xray
// online session tracker.
type Online struct {
	Emails map[string][]string
}

// Count returns the number of online clients.
func (o Online) Count() int { return len(o.Emails) }

// Has reports whether the email is online.
func (o Online) Has(email string) bool {
	_, ok := o.Emails[email]
	return ok
}

// SortedEmails returns the online emails in deterministic order.
func (o Online) SortedEmails() []string {
	if len(o.Emails) == 0 {
		return nil
	}
	emails := make([]string, 0, len(o.Emails))
	for email := range o.Emails {
		emails = append(emails, email)
	}
	sort.Strings(emails)
	return emails
}

// CloneIPs returns a sorted copy of the IP list of an online client.
func (o Online) CloneIPs(email string) []string {
	ips := o.Emails[email]
	if len(ips) == 0 {
		return nil
	}
	out := make([]string, len(ips))
	copy(out, ips)
	sort.Strings(out)
	return out
}

// ParseOnlineUsers parses the JSON of "xray api statsonlineiplist -all",
// "xray api statsgetallonlineusers" or "xray api statsonlineiplist -email".
// The exact shape is not part of the stable CLI contract, so emails and IP
// addresses are extracted generically.
func ParseOnlineUsers(data []byte) Online {
	online := Online{Emails: map[string][]string{}}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return online
	}
	dec := json.NewDecoder(bytes.NewReader(trimmed))
	dec.UseNumber()
	var root any
	if err := dec.Decode(&root); err != nil {
		return online
	}
	ipSets := map[string]map[string]bool{}
	addEmail := func(email string) {
		if email == "" {
			return
		}
		if _, ok := online.Emails[email]; !ok {
			online.Emails[email] = nil
		}
	}
	addIP := func(email, ip string) {
		if email == "" || ip == "" {
			return
		}
		addEmail(email)
		if ipSets[email] == nil {
			ipSets[email] = map[string]bool{}
		}
		ipSets[email][ip] = true
	}
	var walk func(node any, email string)
	walk = func(node any, email string) {
		switch n := node.(type) {
		case map[string]any:
			// An object may declare its user through an "email" field, a
			// "name" field or an embedded "user>>>EMAIL>>>online" key. All
			// of them scope the nested IP addresses to that user.
			current := email
			if str, ok := n["email"].(string); ok && looksLikeEmail(str) {
				current = str
				addEmail(str)
			}
			if str, ok := n["name"].(string); ok {
				if found, ok := emailFromOnlineKey(str); ok {
					current = found
					addEmail(found)
				} else if looksLikeEmail(str) {
					current = str
					addEmail(str)
				}
			}
			for key, value := range n {
				if key == "email" || key == "name" {
					continue
				}
				if found, ok := emailFromOnlineKey(key); ok {
					addEmail(found)
					walk(value, found)
					continue
				}
				if current != "" {
					if ip := normalizeIP(key); ip != "" {
						addIP(current, ip)
					}
				}
				walk(value, current)
			}
		case []any:
			for _, item := range n {
				walk(item, email)
			}
		case string:
			if email != "" {
				if ip := normalizeIP(n); ip != "" {
					addIP(email, ip)
					return
				}
			}
			if looksLikeEmail(n) {
				addEmail(n)
			}
		}
	}
	walk(root, "")
	for email, set := range ipSets {
		ips := make([]string, 0, len(set))
		for ip := range set {
			ips = append(ips, ip)
		}
		sort.Strings(ips)
		online.Emails[email] = ips
	}
	return online
}

// emailFromOnlineKey extracts the user email from a key like
// "user>>>client-1@vpn>>>online".
func emailFromOnlineKey(key string) (string, bool) {
	if !strings.HasSuffix(key, ">>>online") {
		return "", false
	}
	body := strings.TrimSuffix(key, ">>>online")
	if idx := strings.LastIndex(body, ">>>"); idx >= 0 {
		body = body[idx+3:]
	}
	body = strings.TrimSpace(body)
	if !looksLikeEmail(body) {
		return "", false
	}
	return body, true
}

// looksLikeEmail is a lightweight sanity check for Xray user emails.
func looksLikeEmail(value string) bool {
	value = strings.TrimSpace(value)
	if value == "" || len(value) > 320 {
		return false
	}
	if !strings.Contains(value, "@") {
		return false
	}
	return !strings.ContainsAny(value, " \t/\\")
}

// normalizeIP returns the canonical form of an IP address, or "".
func normalizeIP(value string) string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "[")
	value = strings.TrimSuffix(value, "]")
	if value == "" || strings.Contains(value, ">>>") {
		return ""
	}
	addr, err := netip.ParseAddr(value)
	if err != nil {
		return ""
	}
	return addr.String()
}

// SysStats is the subset of "xray api statssys" that the agent reports.
type SysStats struct {
	Goroutines    int
	UptimeMinutes int64
}

// ParseSysStats parses "xray api statssys" output.
func ParseSysStats(data []byte) SysStats {
	var raw struct {
		NumGoroutine int   `json:"NumGoroutine"`
		Goroutines   int   `json:"goroutines"`
		Uptime       int64 `json:"Uptime"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return SysStats{}
	}
	goroutines := raw.NumGoroutine
	if goroutines == 0 {
		goroutines = raw.Goroutines
	}
	return SysStats{Goroutines: goroutines, UptimeMinutes: raw.Uptime}
}
