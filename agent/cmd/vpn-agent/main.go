// Command vpn-agent is the VPN Control Panel node agent.
//
// It runs as a systemd service, applies Xray configuration revisions from the
// panel, reports telemetry and executes panel commands. Subcommands:
//
//	vpn-agent [serve] [--base DIR]
//	vpn-agent uninstall --yes [--base DIR]
//	vpn-agent diagnose [--base DIR]
//	vpn-agent version
//
// The agent only makes outbound HTTPS requests to the panel and never opens a
// listening port. Everything it installs lives under the base directory.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	"time"

	"log/slog"

	"vpn-agent/internal/api"
	"vpn-agent/internal/apply"
	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/metrics"
	"vpn-agent/internal/runner"
	"vpn-agent/internal/state"
	"vpn-agent/internal/systemd"
	"vpn-agent/internal/uninstall"
	"vpn-agent/internal/version"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

// run executes the CLI and returns the process exit code.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		return runServe(nil, stdout, stderr)
	}
	switch args[0] {
	case "serve":
		return runServe(args[1:], stdout, stderr)
	case "uninstall":
		return runUninstall(args[1:], stdout, stderr)
	case "diagnose":
		return runDiagnose(args[1:], stdout, stderr)
	case "version", "--version", "-version":
		fmt.Fprintln(stdout, describeVersion())
		return 0
	case "help", "-h", "--help":
		printUsage(stdout)
		return 0
	default:
		fmt.Fprintf(stderr, "vpn-agent: unknown command %q\n\n", args[0])
		printUsage(stderr)
		return 2
	}
}

// describeVersion returns the one-line version string.
func describeVersion() string {
	return fmt.Sprintf("%s %s (%s/%s, %s)", version.Name, version.Version, runtime.GOOS, runtime.GOARCH, runtime.Version())
}

// printUsage writes the CLI help.
func printUsage(w io.Writer) {
	fmt.Fprint(w, `Usage:
  vpn-agent [serve] [--base DIR] [--log-level LEVEL]
  vpn-agent uninstall --yes [--base DIR]
  vpn-agent diagnose [--base DIR]
  vpn-agent version
  vpn-agent help

Commands:
  serve       run the agent daemon (default)
  uninstall   stop and disable units, remove firewall rules and the base dir
  diagnose    print a diagnostic report as JSON
  version     print the agent version

Flags:
  --base DIR        base directory (default $VPN_AGENT_BASE or /root/config/vpn)
  --log-level L     debug, info, warn or error (serve only)

The agent reads its panel URL, token and tuning knobs from <base>/etc/agent.json.
`)
}

// app bundles the runtime dependencies of the agent.
type app struct {
	cfg         *config.Config
	logger      *slog.Logger
	client      *api.Client
	store       *state.Store
	exec        execx.Runner
	probe       *metrics.XrayProbe
	applier     *apply.Applier
	collector   *metrics.Collector
	buffer      *metrics.Buffer
	uninstaller *uninstall.Uninstaller
}

// build loads the configuration and wires the agent components.
func build(baseFlag, logLevelFlag string, stderr io.Writer) (*app, error) {
	baseDir, err := config.ResolveBaseDir(baseFlag)
	if err != nil {
		return nil, err
	}
	cfg, err := config.Load(baseDir)
	if err != nil {
		return nil, err
	}
	level := parseLogLevel(cfg.LogLevel)
	if strings.TrimSpace(logLevelFlag) != "" {
		level = parseLogLevel(logLevelFlag)
	}
	logger := slog.New(slog.NewTextHandler(stderr, &slog.HandlerOptions{Level: level}))
	for _, warning := range cfg.Warnings {
		logger.Warn(warning)
	}
	paths := cfg.Paths()

	client, err := api.NewClient(api.ClientOptions{
		BaseURL:       cfg.PanelURL,
		Token:         cfg.Token,
		AgentVersion:  version.Version,
		Timeout:       cfg.RequestTimeout(),
		MaxAttempts:   cfg.HTTPMaxAttempts,
		BackoffBase:   500 * time.Millisecond,
		BackoffMax:    10 * time.Second,
		TLSSkipVerify: cfg.TLSSkipVerify,
		Logger:        logger,
	})
	if err != nil {
		return nil, err
	}
	store, err := state.Open(paths.StateFile)
	if err != nil {
		return nil, err
	}
	runnerExec := execx.OSRunner{}
	probe := metrics.NewXrayProbe(runnerExec, cfg.XrayBinary, cfg.XrayAPIAddr, cfg.AssetDir, logger)
	applier := apply.New(apply.Options{
		Paths:          paths,
		Exec:           runnerExec,
		Logger:         logger,
		XrayBin:        cfg.XrayBinary,
		SystemctlBin:   cfg.SystemctlBin,
		XrayUnit:       cfg.XrayUnit,
		XrayAPIAddr:    cfg.XrayAPIAddr,
		AssetDir:       cfg.AssetDir,
		Probe:          probe,
		HealthTimeout:  cfg.HealthTimeout(),
		TestTimeout:    cfg.ApplyTestTimeout(),
		RestartTimeout: cfg.ApplyRestartTimeout(),
	})
	collector := metrics.NewCollector(metrics.Options{
		Paths:              paths,
		Probe:              probe,
		Runner:             runnerExec,
		SystemctlBin:       cfg.SystemctlBin,
		XrayUnit:           cfg.XrayUnit,
		AgentUnit:          cfg.AgentUnit,
		OnlineWindow:       cfg.OnlineWindow(),
		AgentVersion:       version.Version,
		IncludeDiagnostics: cfg.SendDiagnostics,
		Logger:             logger,
	})
	buffer := metrics.NewBuffer(paths.MetricsBuffer, cfg.MetricsBufferTTL())
	buffer.MaxBytes = int64(cfg.MetricsBufferMaxBytes)
	uninstaller := newUninstaller(cfg, runnerExec, logger, false)
	return &app{
		cfg:         cfg,
		logger:      logger,
		client:      client,
		store:       store,
		exec:        runnerExec,
		probe:       probe,
		applier:     applier,
		collector:   collector,
		buffer:      buffer,
		uninstaller: uninstaller,
	}, nil
}

// newUninstaller builds an Uninstaller; CLI uninstall stops the agent unit,
// while the panel-command path stops it after the result has been reported.
func newUninstaller(cfg *config.Config, runner execx.Runner, logger *slog.Logger, stopAgent bool) *uninstall.Uninstaller {
	return uninstall.New(uninstall.Options{
		Paths:        cfg.Paths(),
		Exec:         runner,
		Logger:       logger,
		UnitDir:      cfg.UnitDir,
		AgentUnit:    cfg.AgentUnit,
		XrayUnit:     cfg.XrayUnit,
		SystemctlBin: cfg.SystemctlBin,
		NftBin:       cfg.NftBin,
		UfwBin:       cfg.UfwBin,
		StopAgent:    stopAgent,
	})
}

// newRunner wires a runner around the app dependencies.
func newRunner(application *app) (*runner.Runner, error) {
	return runner.New(runner.Options{
		Config:   application.cfg,
		Client:   application.client,
		Store:    application.store,
		Applier:  application.applier,
		Exec:     application.exec,
		Metrics:  application.collector,
		Buffer:   application.buffer,
		Probe:    application.probe,
		Logger:   application.logger,
		ServerID: application.cfg.ServerID,
		Uninstall: func(ctx context.Context) (uninstall.Report, error) {
			return application.uninstaller.Run(ctx)
		},
		SelfRestart: func(ctx context.Context) error {
			return systemd.RestartNoBlock(ctx, application.exec, application.cfg.SystemctlBin, application.cfg.AgentUnit, 30*time.Second)
		},
		StopSelf: func(ctx context.Context) error {
			return systemd.StopNoBlock(ctx, application.exec, application.cfg.SystemctlBin, application.cfg.AgentUnit, 30*time.Second)
		},
		ArtifactName: application.cfg.EffectiveArtifactName(),
	})
}

// runServe runs the agent daemon.
func runServe(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("serve", flag.ContinueOnError)
	flags.SetOutput(stderr)
	base := flags.String("base", "", "base directory")
	logLevel := flags.String("log-level", "", "log level: debug, info, warn, error")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	application, err := build(*base, *logLevel, stderr)
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent: %v\n", err)
		return 1
	}
	agent, err := newRunner(application)
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent: %v\n", err)
		return 1
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	err = agent.Run(ctx)
	if err == nil || errors.Is(err, runner.ErrUninstallComplete) {
		return 0
	}
	application.logger.Error("agent terminated with an error", "error", err)
	return 1
}

// runUninstall removes the node after an explicit confirmation.
func runUninstall(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("uninstall", flag.ContinueOnError)
	flags.SetOutput(stderr)
	base := flags.String("base", "", "base directory")
	confirmed := flags.Bool("yes", false, "confirm destructive uninstall")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if !*confirmed {
		fmt.Fprintln(stderr, "vpn-agent uninstall: refusing to run without --yes")
		return 2
	}
	application, err := build(*base, "info", stderr)
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent: %v\n", err)
		return 1
	}
	application.uninstaller = newUninstaller(application.cfg, application.exec, application.logger, true)
	report, err := application.uninstaller.Run(context.Background())
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent uninstall: %v\n", err)
		return 1
	}
	encoded, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent uninstall: encode report: %v\n", err)
		return 1
	}
	fmt.Fprintln(stdout, string(encoded))
	return 0
}

// runDiagnose prints a diagnostic report as JSON.
func runDiagnose(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("diagnose", flag.ContinueOnError)
	flags.SetOutput(stderr)
	base := flags.String("base", "", "base directory")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	application, err := build(*base, "info", stderr)
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent: %v\n", err)
		return 1
	}
	agent, err := newRunner(application)
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent: %v\n", err)
		return 1
	}
	report, err := agent.Diagnose(context.Background())
	if err != nil {
		fmt.Fprintf(stderr, "vpn-agent diagnose: %v\n", err)
		return 1
	}
	var pretty any
	if err := json.Unmarshal(report, &pretty); err == nil {
		if encoded, err := json.MarshalIndent(pretty, "", "  "); err == nil {
			report = encoded
		}
	}
	fmt.Fprintln(stdout, string(report))
	return 0
}

// parseLogLevel maps a level name to a slog level.
func parseLogLevel(value string) slog.Level {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
