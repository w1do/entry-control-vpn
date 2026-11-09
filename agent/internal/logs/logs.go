// Package logs parses Xray access and error logs incrementally.
//
// The agent remembers byte offsets in the state file and only consumes newly
// appended complete lines. Parsed access lines produce the accepted, blocked
// (routing to the block outbound) and rejected counters reported to the panel,
// plus per-client last-seen timestamps used as an online fallback.
package logs

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
	"time"
)

// Defaults for reading and tailing logs.
const (
	DefaultMaxReadBytes = 4 << 20
	DefaultTailBytes    = 256 << 10
)

// Verdict is the access log decision for one connection.
type Verdict int

// Access log verdicts.
const (
	VerdictOther Verdict = iota
	VerdictAccepted
	VerdictRejected
)

// Entry is one parsed access log line.
type Entry struct {
	Raw      string
	Time     time.Time
	From     string
	Verdict  Verdict
	Inbound  string
	Outbound string
	Email    string
}

// Blocked reports whether an accepted connection was routed to the block
// outbound (fail-closed routing), as opposed to a protocol rejection.
func (e Entry) Blocked() bool {
	if e.Verdict != VerdictAccepted {
		return false
	}
	return isBlockTag(e.Outbound)
}

// Counts is the number of events of each class in one interval.
type Counts struct {
	Accepted int64
	Blocked  int64
	Rejected int64
}

// Interval is the result of one incremental log collection.
type Interval struct {
	Counts   Counts
	Errors   int64
	LastSeen map[string]time.Time

	AccessOffset    int64
	ErrorOffset     int64
	AccessTruncated bool
	ErrorTruncated  bool
}

// Collector incrementally reads the Xray access and error logs.
type Collector struct {
	AccessPath   string
	ErrorPath    string
	MaxReadBytes int64
}

// Collect reads everything appended to both logs since the given offsets.
// The returned offsets are always safe to persist: a read error on one file
// leaves that file's offset untouched while the other file still advances.
func (c *Collector) Collect(accessOffset, errorOffset int64, now time.Time) (Interval, error) {
	maxBytes := c.MaxReadBytes
	if maxBytes <= 0 {
		maxBytes = DefaultMaxReadBytes
	}
	iv := Interval{
		AccessOffset: accessOffset,
		ErrorOffset:  errorOffset,
		LastSeen:     map[string]time.Time{},
	}
	var errs []error

	accessLines, newAccessOffset, accessTruncated, err := ReadIncremental(c.AccessPath, accessOffset, maxBytes)
	if err != nil {
		errs = append(errs, fmt.Errorf("read access log: %w", err))
	} else {
		iv.AccessOffset = newAccessOffset
		iv.AccessTruncated = accessTruncated
	}
	for _, line := range accessLines {
		entry, ok := ParseAccessLine(line)
		if !ok {
			continue
		}
		switch entry.Verdict {
		case VerdictAccepted:
			iv.Counts.Accepted++
			if entry.Blocked() {
				iv.Counts.Blocked++
			}
		case VerdictRejected:
			iv.Counts.Rejected++
		}
		if entry.Email != "" && !entry.Time.IsZero() {
			iv.LastSeen[entry.Email] = entry.Time
		}
	}

	errorLines, newErrorOffset, errorTruncated, err := ReadIncremental(c.ErrorPath, errorOffset, maxBytes)
	if err != nil {
		errs = append(errs, fmt.Errorf("read error log: %w", err))
	} else {
		iv.ErrorOffset = newErrorOffset
		iv.ErrorTruncated = errorTruncated
	}
	for _, line := range errorLines {
		if IsErrorLine(line) {
			iv.Errors++
		}
	}
	return iv, errors.Join(errs...)
}

// ReadIncremental reads complete lines appended to path after offset.
// It returns the number of lines, the new offset, whether the file shrank
// (rotation/truncation detected) and an error. A missing file resets the
// offset to zero and is not an error.
func ReadIncremental(path string, offset int64, maxBytes int64) ([]string, int64, bool, error) {
	if strings.TrimSpace(path) == "" {
		return nil, offset, false, nil
	}
	if maxBytes <= 0 {
		maxBytes = DefaultMaxReadBytes
	}
	f, err := os.Open(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, 0, false, nil
		}
		return nil, offset, false, err
	}
	defer func() { _ = f.Close() }()
	fi, err := f.Stat()
	if err != nil {
		return nil, offset, false, err
	}
	if fi.IsDir() {
		return nil, offset, false, fmt.Errorf("%s is a directory", path)
	}
	truncated := false
	if offset < 0 || fi.Size() < offset {
		offset = 0
		truncated = true
	}
	if fi.Size() == offset {
		return nil, offset, truncated, nil
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		return nil, offset, truncated, err
	}
	limit := fi.Size() - offset
	if limit > maxBytes {
		limit = maxBytes
	}
	buf := make([]byte, limit)
	n, err := io.ReadFull(f, buf)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, io.EOF) {
		return nil, offset, truncated, err
	}
	data := buf[:n]
	idx := bytes.LastIndexByte(data, '\n')
	if idx < 0 {
		if n == len(buf) && int64(n) == maxBytes {
			// A single line larger than the read cap: drop it to make progress.
			return nil, offset + int64(n), truncated, nil
		}
		return nil, offset, truncated, nil
	}
	consumed := data[:idx+1]
	newOffset := offset + int64(len(consumed))
	text := string(bytes.TrimSuffix(consumed, []byte{'\n'}))
	if text == "" {
		return nil, newOffset, truncated, nil
	}
	return strings.Split(text, "\n"), newOffset, truncated, nil
}

var (
	accessRe  = regexp.MustCompile(`^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+from\s+(\S+)\s+(accepted|rejected)\s*(.*)$`)
	bracketRe = regexp.MustCompile(`\[([^\]\s]+)\s*(?:>>|->)\s*([^\]]+)\]`)
	emailRe   = regexp.MustCompile(`email:\s*(\S+)`)
)

var timeLayouts = []string{
	"2006/01/02 15:04:05.999999999",
	"2006/01/02 15:04:05.999999",
	"2006/01/02 15:04:05",
}

// ParseAccessLine parses one access log line in the Xray plain text format.
// It also accepts JSON access log lines.
func ParseAccessLine(line string) (Entry, bool) {
	line = strings.TrimRight(line, "\r")
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return Entry{}, false
	}
	if strings.HasPrefix(trimmed, "{") {
		return parseJSONAccessLine(line)
	}
	m := accessRe.FindStringSubmatch(line)
	if m == nil {
		return Entry{}, false
	}
	entry := Entry{Raw: line, From: m[2]}
	switch m[3] {
	case "accepted":
		entry.Verdict = VerdictAccepted
	case "rejected":
		entry.Verdict = VerdictRejected
	}
	if t, ok := parseLogTime(m[1]); ok {
		entry.Time = t
	}
	rest := m[4]
	if bm := bracketRe.FindStringSubmatch(rest); bm != nil {
		entry.Inbound = strings.TrimSpace(bm[1])
		entry.Outbound = strings.TrimSpace(bm[2])
	}
	if em := emailRe.FindStringSubmatch(rest); em != nil {
		entry.Email = strings.TrimSpace(em[1])
	}
	return entry, true
}

// parseJSONAccessLine parses the optional JSON access log format.
func parseJSONAccessLine(line string) (Entry, bool) {
	var raw map[string]any
	if err := json.Unmarshal([]byte(line), &raw); err != nil {
		return Entry{}, false
	}
	verdict, ok := jsonVerdict(raw)
	if !ok {
		return Entry{}, false
	}
	entry := Entry{Raw: line, Verdict: verdict}
	entry.From = firstString(raw, "from", "source", "src")
	entry.Email = firstString(raw, "email")
	entry.Inbound = firstString(raw, "inbound", "inbound_tag")
	entry.Outbound = firstString(raw, "outbound", "outbound_tag", "route")
	if ts := firstString(raw, "t", "time", "timestamp", "ts"); ts != "" {
		for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006/01/02 15:04:05.999999", "2006/01/02 15:04:05"} {
			if t, err := time.Parse(layout, ts); err == nil {
				entry.Time = t
				break
			}
		}
	}
	return entry, true
}

// jsonVerdict extracts the verdict from a JSON access log line.
func jsonVerdict(raw map[string]any) (Verdict, bool) {
	for _, key := range []string{"accepted", "rejected"} {
		if v, ok := raw[key]; ok {
			switch vv := v.(type) {
			case bool:
				if vv {
					if key == "accepted" {
						return VerdictAccepted, true
					}
					return VerdictRejected, true
				}
			case string:
				if key == "accepted" {
					return VerdictAccepted, true
				}
				return VerdictRejected, true
			}
		}
	}
	if _, ok := raw["from"]; ok {
		if _, ok := raw["to"]; ok {
			return VerdictAccepted, true
		}
	}
	return VerdictOther, false
}

// IsErrorLine reports whether an error log line represents an error event.
func IsErrorLine(line string) bool {
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return false
	}
	if strings.HasPrefix(trimmed, "{") {
		var raw map[string]any
		if err := json.Unmarshal([]byte(trimmed), &raw); err != nil {
			return false
		}
		level := strings.ToLower(firstString(raw, "level", "severity", "lvl"))
		return level == "error" || level == "fatal" || level == "critical"
	}
	if strings.Contains(trimmed, "[Error]") || strings.Contains(trimmed, "[Fatal]") {
		return true
	}
	lowered := strings.ToLower(trimmed)
	return strings.Contains(lowered, "level=error") || strings.Contains(lowered, "level=fatal")
}

// parseLogTime parses the Xray plain text timestamp.
func parseLogTime(value string) (time.Time, bool) {
	for _, layout := range timeLayouts {
		if t, err := time.Parse(layout, value); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// isBlockTag reports whether an outbound tag is the fail-closed block outbound.
func isBlockTag(tag string) bool {
	fields := strings.Fields(strings.TrimSpace(tag))
	if len(fields) == 0 {
		return false
	}
	name := strings.ToLower(fields[0])
	return name == "block" || name == "blackhole" || strings.Contains(name, "block")
}

// firstString returns the first string value among the given keys.
func firstString(raw map[string]any, keys ...string) string {
	for _, k := range keys {
		if v, ok := raw[k]; ok {
			if s, ok := v.(string); ok {
				return s
			}
		}
	}
	return ""
}

// Tail returns up to lines last non-empty lines of a text file.
// A missing file yields no lines and no error.
func Tail(path string, lines int) ([]string, error) {
	if lines <= 0 {
		lines = 200
	}
	if strings.TrimSpace(path) == "" {
		return nil, nil
	}
	f, err := os.Open(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	defer func() { _ = f.Close() }()
	fi, err := f.Stat()
	if err != nil {
		return nil, err
	}
	start := fi.Size() - DefaultTailBytes
	if start < 0 {
		start = 0
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return nil, err
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return nil, err
	}
	parts := strings.Split(string(data), "\n")
	if start > 0 && len(parts) > 0 {
		parts = parts[1:] // drop the first partial line
	}
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimRight(p, "\r")
		if strings.TrimSpace(p) == "" {
			continue
		}
		out = append(out, p)
	}
	if len(out) > lines {
		out = out[len(out)-lines:]
	}
	return out, nil
}
