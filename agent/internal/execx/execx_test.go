package execx

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestRunSuccess(t *testing.T) {
	var runner OSRunner
	result, err := runner.Run(context.Background(), Command{
		Name: "sh",
		Args: []string{"-c", "printf 'hello'"},
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !result.OK() {
		t.Fatalf("exit code = %d", result.ExitCode)
	}
	if string(result.Stdout) != "hello" {
		t.Fatalf("stdout = %q", string(result.Stdout))
	}
}

func TestRunNonZeroIsNotError(t *testing.T) {
	var runner OSRunner
	result, err := runner.Run(context.Background(), Command{
		Name: "sh",
		Args: []string{"-c", "echo problem >&2; exit 23"},
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if result.OK() {
		t.Fatalf("expected a non-zero exit")
	}
	if result.ExitCode != 23 {
		t.Fatalf("exit code = %d", result.ExitCode)
	}
	if !strings.Contains(result.Output(), "problem") {
		t.Fatalf("output = %q", result.Output())
	}
}

func TestRunEnvAndStdin(t *testing.T) {
	var runner OSRunner
	result, err := runner.Run(context.Background(), Command{
		Name:  "sh",
		Args:  []string{"-c", "cat; printf '%s' \"$AGENT_TEST\""},
		Env:   []string{"AGENT_TEST=value"},
		Stdin: []byte("stdin-data"),
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	output := string(result.Stdout)
	if !strings.Contains(output, "stdin-data") || !strings.Contains(output, "value") {
		t.Fatalf("output = %q", output)
	}
}

func TestRunMissingBinary(t *testing.T) {
	var runner OSRunner
	if _, err := runner.Run(context.Background(), Command{Name: "/nonexistent/vpn-agent-test-binary"}); err == nil {
		t.Fatalf("expected an error for a missing binary")
	}
}

func TestRunTimeout(t *testing.T) {
	var runner OSRunner
	start := time.Now()
	result, err := runner.Run(context.Background(), Command{
		Name:    "sleep",
		Args:    []string{"5"},
		Timeout: 100 * time.Millisecond,
	})
	if err == nil {
		t.Fatalf("expected a timeout error")
	}
	if !result.TimedOut {
		t.Fatalf("expected TimedOut to be true")
	}
	if elapsed := time.Since(start); elapsed > 3*time.Second {
		t.Fatalf("command was not killed in time: %s", elapsed)
	}
}

func TestRunEmptyName(t *testing.T) {
	var runner OSRunner
	if _, err := runner.Run(context.Background(), Command{}); err == nil {
		t.Fatalf("expected an error for an empty command name")
	}
}
