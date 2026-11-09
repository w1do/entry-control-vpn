package logs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// real log lines captured from Xray-core 26.11.9.
const (
	lineAccepted      = "2026/11/09 22:13:37.279010 from tcp:127.0.0.1:47017 accepted tcp:127.0.0.1:28080 [socks-in >> direct]"
	lineAcceptedEmail = "2026/11/09 22:16:17.167479 from 127.0.0.1:47147 accepted tcp:127.0.0.1:28082 [vless-in >> direct] email: client-1@vpn"
	lineBlocked       = "2026/11/09 22:13:37.279010 from 198.51.100.7:5555 accepted tcp:blocked.example:443 [vless-in >> block] email: client-1@vpn"
	lineBlockedLegacy = "2026/11/09 22:39:03.138242 from tcp:127.0.0.1:43627 accepted tcp:blocked.example:80 [socks-in -> block]"
	lineRejected      = "2026/11/09 22:16:20.180044 from 127.0.0.1:46705 rejected  proxy/vless/encoding: invalid request version"
)

func TestParseAccessLine(t *testing.T) {
	entry, ok := ParseAccessLine(lineAcceptedEmail)
	if !ok {
		t.Fatalf("line was not parsed")
	}
	if entry.Verdict != VerdictAccepted {
		t.Errorf("verdict = %v", entry.Verdict)
	}
	if entry.Inbound != "vless-in" || entry.Outbound != "direct" {
		t.Errorf("tags = %q -> %q", entry.Inbound, entry.Outbound)
	}
	if entry.Email != "client-1@vpn" {
		t.Errorf("email = %q", entry.Email)
	}
	if entry.Time.IsZero() {
		t.Errorf("timestamp was not parsed")
	}
	if entry.Blocked() {
		t.Errorf("direct route must not be blocked")
	}

	for _, line := range []string{lineBlocked, lineBlockedLegacy} {
		blocked, ok := ParseAccessLine(line)
		if !ok || !blocked.Blocked() {
			t.Fatalf("blocked line was not classified as blocked: %+v", blocked)
		}
	}

	rejected, ok := ParseAccessLine(lineRejected)
	if !ok || rejected.Verdict != VerdictRejected {
		t.Fatalf("rejected line = %+v, %v", rejected, ok)
	}
	if rejected.Blocked() {
		t.Errorf("rejected line must not be counted as blocked")
	}

	if _, ok := ParseAccessLine("not a log line"); ok {
		t.Errorf("garbage line was parsed")
	}
}

func TestParseJSONAccessLine(t *testing.T) {
	line := `{"t":"2026-11-09T22:16:17Z","from":"203.0.113.7:555","accepted":true,"inbound":"vless-in","outbound":"direct","email":"a@b"}`
	entry, ok := ParseAccessLine(line)
	if !ok {
		t.Fatalf("JSON line was not parsed")
	}
	if entry.Verdict != VerdictAccepted || entry.Email != "a@b" || entry.Outbound != "direct" {
		t.Fatalf("entry = %+v", entry)
	}
}

func TestIsErrorLine(t *testing.T) {
	cases := map[string]bool{
		"2026/11/09 22:16:20.180086 [Info] connection ends": false,
		"2026/11/09 22:16:20.180086 [Error] proxy failed":   true,
		"2026/11/09 22:16:20.180086 [Warning] minor":        false,
		`{"level":"error","msg":"failed"}`:                  true,
		`{"level":"info","msg":"ok"}`:                       false,
		"":                                                  false,
	}
	for line, want := range cases {
		if got := IsErrorLine(line); got != want {
			t.Errorf("IsErrorLine(%q) = %v, want %v", line, got, want)
		}
	}
}

func TestReadIncremental(t *testing.T) {
	path := filepath.Join(t.TempDir(), "access.log")
	if err := os.WriteFile(path, []byte(lineAccepted+"\n"+lineRejected+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	lines, offset, truncated, err := ReadIncremental(path, 0, DefaultMaxReadBytes)
	if err != nil {
		t.Fatalf("ReadIncremental: %v", err)
	}
	if len(lines) != 2 || truncated {
		t.Fatalf("lines = %d, truncated = %v", len(lines), truncated)
	}
	if offset <= 0 {
		t.Fatalf("offset = %d", offset)
	}
	// Nothing new.
	lines, newOffset, _, err := ReadIncremental(path, offset, DefaultMaxReadBytes)
	if err != nil || len(lines) != 0 || newOffset != offset {
		t.Fatalf("second read: lines=%d offset=%d err=%v", len(lines), newOffset, err)
	}
	// Append a partial line, then complete it.
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(lineAccepted); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	lines, partialOffset, _, err := ReadIncremental(path, offset, DefaultMaxReadBytes)
	if err != nil || len(lines) != 0 || partialOffset != offset {
		t.Fatalf("partial line must not be consumed: lines=%d offset=%d err=%v", len(lines), partialOffset, err)
	}
	f, err = os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("\n"); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	lines, _, _, err = ReadIncremental(path, offset, DefaultMaxReadBytes)
	if err != nil || len(lines) != 1 {
		t.Fatalf("completed line: lines=%d err=%v", len(lines), err)
	}
}

func TestReadIncrementalTruncation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "access.log")
	if err := os.WriteFile(path, []byte(lineAccepted+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, offset, _, err := ReadIncremental(path, 0, DefaultMaxReadBytes)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("short\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	lines, newOffset, truncated, err := ReadIncremental(path, offset, DefaultMaxReadBytes)
	if err != nil {
		t.Fatalf("ReadIncremental: %v", err)
	}
	if !truncated {
		t.Fatalf("expected truncation detection")
	}
	if len(lines) != 1 || newOffset == offset {
		t.Fatalf("lines=%d offset=%d", len(lines), newOffset)
	}
}

func TestReadIncrementalMissingFile(t *testing.T) {
	lines, offset, truncated, err := ReadIncremental(filepath.Join(t.TempDir(), "nope.log"), 42, DefaultMaxReadBytes)
	if err != nil {
		t.Fatalf("ReadIncremental: %v", err)
	}
	if len(lines) != 0 || offset != 0 || truncated {
		t.Fatalf("missing file: lines=%d offset=%d truncated=%v", len(lines), offset, truncated)
	}
}

func TestCollector(t *testing.T) {
	dir := t.TempDir()
	accessPath := filepath.Join(dir, "access.log")
	errorPath := filepath.Join(dir, "xray-error.log")
	access := strings.Join([]string{lineAccepted, lineAcceptedEmail, lineBlocked, lineRejected}, "\n") + "\n"
	errors := "2026/11/09 22:00:00 [Info] started\n2026/11/09 22:00:01 [Error] boom\n"
	if err := os.WriteFile(accessPath, []byte(access), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(errorPath, []byte(errors), 0o600); err != nil {
		t.Fatal(err)
	}
	collector := &Collector{AccessPath: accessPath, ErrorPath: errorPath}
	interval, err := collector.Collect(0, 0, time.Now())
	if err != nil {
		t.Fatalf("Collect: %v", err)
	}
	if interval.Counts.Accepted != 3 {
		t.Errorf("accepted = %d", interval.Counts.Accepted)
	}
	if interval.Counts.Blocked != 1 {
		t.Errorf("blocked = %d", interval.Counts.Blocked)
	}
	if interval.Counts.Rejected != 1 {
		t.Errorf("rejected = %d", interval.Counts.Rejected)
	}
	if interval.Errors != 1 {
		t.Errorf("errors = %d", interval.Errors)
	}
	if ts, ok := interval.LastSeen["client-1@vpn"]; !ok || ts.IsZero() {
		t.Errorf("last seen = %v", interval.LastSeen)
	}
	if interval.AccessOffset <= 0 || interval.ErrorOffset <= 0 {
		t.Errorf("offsets = %d %d", interval.AccessOffset, interval.ErrorOffset)
	}
	second, err := collector.Collect(interval.AccessOffset, interval.ErrorOffset, time.Now())
	if err != nil {
		t.Fatalf("second Collect: %v", err)
	}
	if second.Counts != (Counts{}) || second.Errors != 0 {
		t.Fatalf("second interval must be empty: %+v errors=%d", second.Counts, second.Errors)
	}
}

func TestTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "apply.log")
	var builder strings.Builder
	for i := 0; i < 300; i++ {
		builder.WriteString("line ")
		builder.WriteString(strings.Repeat("x", i%5))
		builder.WriteString("\n")
	}
	if err := os.WriteFile(path, []byte(builder.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	lines, err := Tail(path, 200)
	if err != nil {
		t.Fatalf("Tail: %v", err)
	}
	if len(lines) != 200 {
		t.Fatalf("tail lines = %d", len(lines))
	}
	if lines[len(lines)-1] != "line xxxx" {
		t.Errorf("last line = %q", lines[len(lines)-1])
	}
	missing, err := Tail(filepath.Join(t.TempDir(), "missing.log"), 10)
	if err != nil || missing != nil {
		t.Fatalf("missing file: %v %v", missing, err)
	}
}
