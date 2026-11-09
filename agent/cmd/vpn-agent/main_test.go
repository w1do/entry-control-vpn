package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestRunVersion(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"version"}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d, stderr = %s", code, stderr.String())
	}
	output := stdout.String()
	if !strings.Contains(output, "vpn-agent") || !strings.Contains(output, "linux/") {
		t.Fatalf("version output = %q", output)
	}
}

func TestRunHelp(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"help"}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	usage := stdout.String()
	for _, command := range []string{"serve", "uninstall", "diagnose", "version"} {
		if !strings.Contains(usage, command) {
			t.Errorf("usage does not mention %q", command)
		}
	}
}

func TestRunUnknownCommand(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"frobnicate"}, &stdout, &stderr); code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr.String(), "unknown command") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunUninstallRequiresConfirmation(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"uninstall"}, &stdout, &stderr); code != 2 {
		t.Fatalf("exit code = %d, want 2", code)
	}
	if !strings.Contains(stderr.String(), "--yes") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunServeMissingConfig(t *testing.T) {
	var stdout, stderr bytes.Buffer
	base := t.TempDir()
	if code := run([]string{"serve", "--base", base}, &stdout, &stderr); code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
	if !strings.Contains(stderr.String(), "agent.json") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunDiagnoseMissingConfig(t *testing.T) {
	var stdout, stderr bytes.Buffer
	base := t.TempDir()
	if code := run([]string{"diagnose", "--base", base}, &stdout, &stderr); code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
}

func TestRunUninstallMissingConfig(t *testing.T) {
	var stdout, stderr bytes.Buffer
	base := t.TempDir()
	if code := run([]string{"uninstall", "--yes", "--base", base}, &stdout, &stderr); code != 1 {
		t.Fatalf("exit code = %d, want 1", code)
	}
}

func TestParseLogLevel(t *testing.T) {
	cases := map[string]string{
		"debug": "DEBUG",
		"warn":  "WARN",
		"error": "ERROR",
		"info":  "INFO",
		"":      "INFO",
	}
	for input, want := range cases {
		if got := parseLogLevel(input).String(); got != want {
			t.Errorf("parseLogLevel(%q) = %s, want %s", input, got, want)
		}
	}
}
