// Package execx is a tiny abstraction over external command execution.
//
// Every side effecting operation of the agent (xray validation, systemctl,
// nft, ufw) goes through a Runner. The indirection keeps the runtime testable
// with fake xray/systemctl shell scripts without adding third party modules.
package execx

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// Command describes one external command invocation.
type Command struct {
	Name    string
	Args    []string
	Env     []string // appended to the current process environment
	Dir     string
	Timeout time.Duration
	Stdin   []byte
}

// Result is the captured outcome of a command that was started.
// A non-zero ExitCode is not reported as an error: callers decide whether a
// non-zero exit is fatal for their operation.
type Result struct {
	Stdout   []byte
	Stderr   []byte
	ExitCode int
	Duration time.Duration
	TimedOut bool
}

// OK reports whether the command exited with status 0.
func (r Result) OK() bool { return r.ExitCode == 0 }

// Output returns stdout and stderr combined, trimmed of surrounding whitespace.
func (r Result) Output() string {
	var b strings.Builder
	b.Write(r.Stdout)
	if len(r.Stdout) > 0 && len(r.Stderr) > 0 && !bytes.HasSuffix(r.Stdout, []byte("\n")) {
		b.WriteByte('\n')
	}
	b.Write(r.Stderr)
	return strings.TrimSpace(b.String())
}

// Runner runs external commands.
type Runner interface {
	Run(ctx context.Context, cmd Command) (Result, error)
}

// OSRunner runs commands as child processes of the agent.
type OSRunner struct{}

// Run implements Runner. A command that starts and exits non-zero returns a
// Result with the corresponding ExitCode and a nil error. Errors are reserved
// for commands that could not be started (missing binary, deadline, ...).
func (OSRunner) Run(ctx context.Context, c Command) (Result, error) {
	if strings.TrimSpace(c.Name) == "" {
		return Result{}, errors.New("execx: empty command name")
	}
	runCtx := ctx
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		runCtx, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	// #nosec G204 -- the binary path and arguments come from the agent config
	// and from systemd unit names written by the panel, not from untrusted input.
	cmd := exec.CommandContext(runCtx, c.Name, c.Args...)
	cmd.Dir = c.Dir
	if len(c.Env) > 0 {
		cmd.Env = append(os.Environ(), c.Env...)
	}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if len(c.Stdin) > 0 {
		cmd.Stdin = bytes.NewReader(c.Stdin)
	}
	start := time.Now()
	err := cmd.Run()
	res := Result{
		Stdout:   stdout.Bytes(),
		Stderr:   stderr.Bytes(),
		Duration: time.Since(start),
		TimedOut: errors.Is(runCtx.Err(), context.DeadlineExceeded),
	}
	if err == nil {
		return res, nil
	}
	if res.TimedOut {
		return res, fmt.Errorf("execx: %s timed out after %s", c.Name, c.Timeout)
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		res.ExitCode = exitErr.ExitCode()
		if res.ExitCode == 0 {
			res.ExitCode = -1
		}
		return res, nil
	}
	res.ExitCode = -1
	if res.TimedOut {
		return res, fmt.Errorf("execx: %s timed out after %s", c.Name, c.Timeout)
	}
	return res, fmt.Errorf("execx: run %s: %w", c.Name, err)
}
