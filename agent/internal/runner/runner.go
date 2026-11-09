// Package runner implements the main agent loop: registration, heartbeats,
// configuration polling, metrics delivery and panel command handling.
package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"runtime"
	"strings"
	"sync"
	"time"

	"log/slog"

	"vpn-agent/internal/api"
	"vpn-agent/internal/apply"
	"vpn-agent/internal/config"
	"vpn-agent/internal/execx"
	"vpn-agent/internal/metrics"
	"vpn-agent/internal/state"
	"vpn-agent/internal/systemd"
	"vpn-agent/internal/uninstall"
	"vpn-agent/internal/version"
)

// ErrUninstallComplete signals that the node has been uninstalled and the
// process must exit with success.
var ErrUninstallComplete = errors.New("runner: uninstall complete")

// Default intervals used when neither config nor Options provide a value.
const (
	DefaultRetryMin = 2 * time.Second
	DefaultRetryMax = 60 * time.Second
)

// Intervals overrides the loop periods. Zero values fall back to the config.
type Intervals struct {
	Heartbeat   time.Duration
	ConfigPoll  time.Duration
	Metrics     time.Duration
	RetryMin    time.Duration
	RetryMax    time.Duration
	AuthBackoff time.Duration
}

// Options configures the Runner.
type Options struct {
	Config      *config.Config
	Client      *api.Client
	Store       *state.Store
	Applier     *apply.Applier
	Exec        execx.Runner
	Metrics     *metrics.Collector
	Buffer      *metrics.Buffer
	Probe       *metrics.XrayProbe
	Logger      *slog.Logger
	Intervals   Intervals
	Now         func() time.Time
	ProcUptime  systemd.ProcUptime
	Uninstall   func(context.Context) (uninstall.Report, error)
	SelfRestart func(context.Context) error
	// StopSelf stops the agent's own systemd unit without blocking. It is
	// called after a panel uninstall command has been reported.
	StopSelf     func(context.Context) error
	Version      string
	Capabilities []string
	ArtifactName string
	ServerID     int64
}

// Runner drives the agent protocol.
type Runner struct {
	o      Options
	logger *slog.Logger
	now    func() time.Time

	mu                 sync.Mutex
	registered         bool
	remoteRevision     int64
	authBlockedUntil   time.Time
	forceReapply       bool
	applyDegraded      string
	metricsDegraded    string
	pendingSelfRestart bool
	pendingSelfStop    bool
	uninstallComplete  bool
	shutdown           bool
}

// New validates the options and returns a Runner.
func New(o Options) (*Runner, error) {
	if o.Config == nil {
		return nil, errors.New("runner: config is required")
	}
	if o.Client == nil {
		return nil, errors.New("runner: API client is required")
	}
	if o.Store == nil {
		return nil, errors.New("runner: state store is required")
	}
	if o.Applier == nil {
		return nil, errors.New("runner: applier is required")
	}
	if o.Metrics == nil {
		return nil, errors.New("runner: metrics collector is required")
	}
	if o.Exec == nil {
		return nil, errors.New("runner: command runner is required")
	}
	if o.Buffer == nil {
		return nil, errors.New("runner: metrics buffer is required")
	}
	if o.Uninstall == nil {
		o.Uninstall = func(context.Context) (uninstall.Report, error) {
			return uninstall.Report{}, errors.New("runner: uninstall is not configured")
		}
	}
	if o.Logger == nil {
		o.Logger = slog.Default()
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.ProcUptime == nil {
		o.ProcUptime = systemd.DefaultProcUptime
	}
	if o.Version == "" {
		o.Version = version.Version
	}
	if len(o.Capabilities) == 0 {
		o.Capabilities = []string{"apply", "rollback", "metrics", "diagnose", "uninstall", "self-update", "commands"}
	}
	if o.ArtifactName == "" {
		o.ArtifactName = o.Config.EffectiveArtifactName()
	}
	if o.ServerID == 0 {
		o.ServerID = o.Config.ServerID
	}
	return &Runner{o: o, logger: o.Logger, now: o.Now}, nil
}

// Run executes the agent until the context is cancelled or the node is
// uninstalled.
func (r *Runner) Run(ctx context.Context) error {
	if err := r.Prepare(ctx); err != nil {
		return err
	}
	r.logger.Info("agent starting",
		"version", r.o.Version,
		"server_id", r.o.ServerID,
		"panel_url", r.o.Config.PanelURL,
		"base_dir", r.o.Config.Paths().BaseDir,
	)
	if !r.registerWithRetry(ctx) {
		return nil
	}
	heartbeat, poll, metricsInterval := r.intervals()
	heartbeatTicker := time.NewTicker(heartbeat)
	defer heartbeatTicker.Stop()
	pollTicker := time.NewTicker(poll)
	defer pollTicker.Stop()
	metricsTicker := time.NewTicker(metricsInterval)
	defer metricsTicker.Stop()

	if err := r.runCycle(ctx, "config poll", r.ConfigPollOnce); err != nil {
		return err
	}
	if err := r.runCycle(ctx, "heartbeat", r.HeartbeatOnce); err != nil {
		return err
	}
	for {
		select {
		case <-ctx.Done():
			r.logger.Info("agent stopping")
			return nil
		case <-heartbeatTicker.C:
			if err := r.runCycle(ctx, "heartbeat", r.HeartbeatOnce); err != nil {
				return err
			}
		case <-pollTicker.C:
			if err := r.runCycle(ctx, "config poll", r.ConfigPollOnce); err != nil {
				return err
			}
		case <-metricsTicker.C:
			if err := r.runCycle(ctx, "metrics", r.MetricsOnce); err != nil {
				return err
			}
		}
	}
}

// runCycle runs one loop step, stopping the loop only for fatal outcomes.
func (r *Runner) runCycle(ctx context.Context, name string, fn func(context.Context) error) error {
	err := fn(ctx)
	if err == nil {
		return nil
	}
	if errors.Is(err, ErrUninstallComplete) || errors.Is(err, context.Canceled) {
		return err
	}
	r.logger.Warn("loop step failed", "step", name, "error", err)
	return nil
}

// Prepare ensures the base directory structure and retries an apply result
// that was not delivered before the previous agent shutdown.
func (r *Runner) Prepare(ctx context.Context) error {
	if err := r.o.Config.EnsureDirs(); err != nil {
		return err
	}
	if r.o.Store.LoadWarning != nil {
		r.logger.Warn("state file was reset", "error", r.o.Store.LoadWarning)
	}
	if err := r.resumePendingApply(ctx); err != nil {
		r.logger.Warn("could not resume pending apply result", "error", err)
	}
	return nil
}

// intervals resolves the effective loop periods.
func (r *Runner) intervals() (heartbeat, poll, metricsInterval time.Duration) {
	heartbeat = r.o.Config.HeartbeatInterval()
	poll = r.o.Config.ConfigPollInterval()
	metricsInterval = r.o.Config.MetricsInterval()
	if r.o.Intervals.Heartbeat > 0 {
		heartbeat = r.o.Intervals.Heartbeat
	}
	if r.o.Intervals.ConfigPoll > 0 {
		poll = r.o.Intervals.ConfigPoll
	}
	if r.o.Intervals.Metrics > 0 {
		metricsInterval = r.o.Intervals.Metrics
	}
	return heartbeat, poll, metricsInterval
}

// retryDelays resolves the retry backoff bounds.
func (r *Runner) retryDelays() (minDelay, maxDelay time.Duration) {
	minDelay = DefaultRetryMin
	maxDelay = r.o.Config.RetryMaxBackoff()
	if r.o.Intervals.RetryMin > 0 {
		minDelay = r.o.Intervals.RetryMin
	}
	if r.o.Intervals.RetryMax > 0 {
		maxDelay = r.o.Intervals.RetryMax
	}
	if maxDelay < minDelay {
		maxDelay = minDelay
	}
	return minDelay, maxDelay
}

// authBackoff returns how long the agent stays quiet after a 401/403.
func (r *Runner) authBackoff() time.Duration {
	if r.o.Intervals.AuthBackoff > 0 {
		return r.o.Intervals.AuthBackoff
	}
	return r.o.Config.AuthBackoff()
}

// registerWithRetry registers the node, retrying until success or shutdown.
func (r *Runner) registerWithRetry(ctx context.Context) bool {
	minDelay, maxDelay := r.retryDelays()
	delay := minDelay
	for {
		if ctx.Err() != nil {
			return false
		}
		err := r.RegisterOnce(ctx)
		if err == nil {
			return true
		}
		if errors.Is(err, api.ErrUnauthorized) {
			r.markAuthBlocked()
			r.logger.Warn("panel rejected the agent token; keeping the last applied configuration", "error", err)
			delay = r.authBackoff()
		} else {
			r.logger.Warn("registration failed, retrying", "error", err, "retry_in", delay)
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return false
		case <-timer.C:
		}
		delay *= 2
		if delay > maxDelay {
			delay = maxDelay
		}
	}
}

// RegisterOnce registers the node in the panel.
func (r *Runner) RegisterOnce(ctx context.Context) error {
	hostname, _ := hostnameOrUnknown()
	req := &api.RegisterRequest{
		AgentVersion: r.o.Version,
		XrayVersion:  r.xrayVersion(ctx),
		Hostname:     hostname,
		OS:           runtime.GOOS,
		Arch:         runtime.GOARCH,
		Capabilities: r.o.Capabilities,
	}
	resp, err := r.o.Client.Register(ctx, req)
	if err != nil {
		return err
	}
	now := r.now().UTC()
	if err := r.o.Store.Update(func(s *state.State) error {
		s.LastRegisterAt = &now
		return nil
	}); err != nil {
		r.logger.Warn("could not persist registration time", "error", err)
	}
	r.mu.Lock()
	r.registered = true
	r.authBlockedUntil = time.Time{}
	r.remoteRevision = int64(resp.Revision)
	if r.o.ServerID == 0 && resp.ServerID != 0 {
		r.o.ServerID = resp.ServerID
	}
	r.mu.Unlock()
	r.logger.Info("registered with panel", "server_id", r.o.ServerID, "panel_revision", resp.Revision)
	return nil
}

// HeartbeatOnce sends a heartbeat and processes panel commands.
func (r *Runner) HeartbeatOnce(ctx context.Context) error {
	if r.authBlocked() {
		return nil
	}
	snap := r.o.Store.Snapshot()
	status := r.localXrayStatus(ctx)
	agentState := r.unitState(ctx, r.o.Config.AgentUnit)
	xrayState := r.unitState(ctx, r.o.Config.XrayUnit)
	req := &api.HeartbeatRequest{
		Revision:       snap.Revision,
		Xray:           status,
		ConfigSHA256:   snap.ConfigSHA256,
		Units:          map[string]string{"xray": xrayState, "agent": agentState},
		DegradedReason: r.degradedReason(status.Active),
	}
	resp, err := r.o.Client.Heartbeat(ctx, req)
	if err != nil {
		if errors.Is(err, api.ErrUnauthorized) {
			r.markAuthBlocked()
			r.logger.Warn("panel rejected the agent token; keeping the last applied configuration", "error", err)
			return nil
		}
		return err
	}
	r.clearAuthBlocked()
	now := r.now().UTC()
	if err := r.o.Store.Update(func(s *state.State) error {
		s.LastHeartbeatAt = &now
		return nil
	}); err != nil {
		r.logger.Warn("could not persist heartbeat time", "error", err)
	}
	r.mu.Lock()
	r.remoteRevision = int64(resp.Revision)
	r.mu.Unlock()
	if int64(resp.Revision) != snap.Revision && (resp.ApplyPending || int64(resp.Revision) > snap.Revision) {
		if err := r.ConfigPollOnce(ctx); err != nil {
			if errors.Is(err, ErrUninstallComplete) {
				return err
			}
			r.logger.Warn("config poll after heartbeat failed", "error", err)
		}
	}
	return r.handleCommands(ctx, resp.Commands)
}

// handleCommands processes a list of panel commands sequentially.
func (r *Runner) handleCommands(ctx context.Context, commands []api.Command) error {
	for _, command := range commands {
		if err := r.HandleCommand(ctx, command); err != nil {
			if errors.Is(err, ErrUninstallComplete) {
				return err
			}
			r.logger.Warn("command failed", "command_id", command.ID, "type", command.Type, "error", err)
		}
	}
	return nil
}

// ConfigPollOnce fetches and applies a new configuration revision.
func (r *Runner) ConfigPollOnce(ctx context.Context) error {
	if r.authBlocked() {
		return nil
	}
	snap := r.o.Store.Snapshot()
	r.mu.Lock()
	force := r.forceReapply
	r.forceReapply = false
	r.mu.Unlock()
	revision := snap.Revision
	if force {
		revision = api.ForceRevision
	}
	resp, err := r.o.Client.GetConfig(ctx, revision)
	if err != nil {
		if errors.Is(err, api.ErrUnauthorized) {
			r.markAuthBlocked()
			r.logger.Warn("panel rejected the agent token while fetching config", "error", err)
			return nil
		}
		return err
	}
	if resp == nil {
		if force {
			return errors.New("panel returned no configuration for a forced reapply")
		}
		return nil
	}
	return r.applyRevision(ctx, int64(resp.Revision), resp.Config, resp.SHA256)
}

// applyRevision applies one configuration revision and reports the outcome.
func (r *Runner) applyRevision(ctx context.Context, revision int64, configJSON []byte, expectedSHA string) error {
	raw := unwrapJSONString(configJSON)
	now := r.now().UTC()
	if err := r.o.Store.Update(func(s *state.State) error {
		s.PendingApply = &state.PendingApply{
			Revision:  revision,
			Status:    state.ApplyInProgress,
			SHA256:    expectedSHA,
			UpdatedAt: now,
		}
		return nil
	}); err != nil {
		return fmt.Errorf("persist pending apply: %w", err)
	}
	r.logger.Info("applying configuration revision", "revision", revision, "sha256", expectedSHA)
	result := r.o.Applier.Apply(ctx, revision, raw, expectedSHA)
	if err := r.o.Store.Update(func(s *state.State) error {
		if result.Applied() {
			s.Revision = revision
			s.ConfigSHA256 = result.SHA256
			s.AppliedAt = &now
		}
		s.PendingApply = &state.PendingApply{
			Revision:  revision,
			Status:    result.Status,
			Error:     result.Error,
			LogTail:   result.LogTail,
			SHA256:    result.SHA256,
			UpdatedAt: now,
		}
		s.DegradedReason = result.DegradedReason
		return nil
	}); err != nil {
		return fmt.Errorf("persist apply result: %w", err)
	}
	r.mu.Lock()
	r.applyDegraded = result.DegradedReason
	r.mu.Unlock()
	if err := r.postPendingApply(ctx); err != nil {
		r.logger.Warn("could not deliver apply result, will retry", "error", err)
		return nil
	}
	if result.Applied() {
		r.logger.Info("configuration applied", "revision", revision, "degraded", result.DegradedReason != "")
	} else {
		r.logger.Warn("configuration apply failed", "revision", revision, "error", result.Error)
	}
	return nil
}

// resumePendingApply delivers an apply result left over from a previous run.
func (r *Runner) resumePendingApply(ctx context.Context) error {
	snap := r.o.Store.Snapshot()
	if snap.PendingApply == nil {
		return nil
	}
	if snap.PendingApply.Status == state.ApplyInProgress {
		now := r.now().UTC()
		if err := r.o.Store.Update(func(s *state.State) error {
			if s.PendingApply == nil {
				return nil
			}
			s.PendingApply.Status = state.ApplyFailed
			s.PendingApply.Error = fmt.Sprintf("agent restarted while applying revision %d", s.PendingApply.Revision)
			s.PendingApply.UpdatedAt = now
			return nil
		}); err != nil {
			return err
		}
	}
	return r.postPendingApply(ctx)
}

// postPendingApply reports and clears the pending apply result.
func (r *Runner) postPendingApply(ctx context.Context) error {
	snap := r.o.Store.Snapshot()
	pending := snap.PendingApply
	if pending == nil {
		return nil
	}
	res := &api.ApplyResult{
		Revision: pending.Revision,
		Status:   pending.Status,
		Error:    pending.Error,
		LogTail:  pending.LogTail,
		SHA256:   pending.SHA256,
	}
	if err := r.o.Client.PostApplyResult(ctx, res); err != nil {
		if errors.Is(err, api.ErrUnauthorized) {
			r.markAuthBlocked()
		}
		return err
	}
	return r.o.Store.Update(func(s *state.State) error {
		if s.PendingApply != nil && s.PendingApply.Revision == pending.Revision {
			s.PendingApply = nil
		}
		return nil
	})
}

// MetricsOnce collects metrics, replays the offline buffer and sends the batch.
func (r *Runner) MetricsOnce(ctx context.Context) error {
	snap := r.o.Store.Snapshot()
	collected, err := r.o.Metrics.Collect(ctx, metrics.Previous{
		Counters:   snap.Counters,
		LastSeen:   snap.LastSeen,
		LogOffsets: snap.LogOffsets,
	}, snap.Revision)
	if err != nil {
		return err
	}
	r.mu.Lock()
	r.metricsDegraded = strings.Join(collected.Degraded, "; ")
	r.mu.Unlock()
	if r.authBlocked() {
		return r.bufferAndCommit(collected)
	}
	buffered, err := r.o.Buffer.Len()
	if err != nil {
		r.logger.Warn("could not read metrics buffer", "error", err)
	}
	if buffered > 0 {
		replayed, remaining, replayErr := r.o.Buffer.Replay(ctx, func(ctx context.Context, batch *api.MetricsBatch) error {
			_, err := r.o.Client.PostMetrics(ctx, batch)
			return err
		}, r.o.Config.ReplayBatchLimit)
		if replayed > 0 || remaining > 0 || replayErr != nil {
			r.logger.Info("replayed buffered metrics", "sent", replayed, "remaining", remaining)
		}
		if replayErr != nil {
			if errors.Is(replayErr, api.ErrUnauthorized) {
				r.markAuthBlocked()
				r.logger.Warn("panel rejected the agent token while replaying metrics", "error", replayErr)
			} else {
				r.logger.Warn("metrics replay failed, keeping the buffer", "error", replayErr)
			}
			return r.bufferAndCommit(collected)
		}
		if remaining > 0 {
			return r.bufferAndCommit(collected)
		}
	}
	resp, err := r.o.Client.PostMetrics(ctx, collected.Batch)
	if err != nil {
		if errors.Is(err, api.ErrUnauthorized) {
			r.markAuthBlocked()
			r.logger.Warn("panel rejected the agent token while sending metrics", "error", err)
		} else {
			r.logger.Warn("metrics delivery failed, buffering", "error", err)
		}
		return r.bufferAndCommit(collected)
	}
	r.clearAuthBlocked()
	if err := r.commitMetrics(collected); err != nil {
		return err
	}
	if resp != nil {
		return r.handleCommands(ctx, resp.Commands)
	}
	return nil
}

// bufferAndCommit durably buffers the batch and then advances the local state.
func (r *Runner) bufferAndCommit(collected *metrics.Snapshot) error {
	if err := r.o.Buffer.Append(collected.Batch); err != nil {
		r.logger.Warn("could not buffer metrics", "error", err)
	}
	r.mu.Lock()
	remaining := r.metricsDegraded
	r.mu.Unlock()
	if remaining != "" {
		r.logger.Debug("metrics collected with degraded parts", "degraded", remaining)
	}
	return r.commitMetrics(collected)
}

// commitMetrics persists the new counter baseline, log offsets and last-seen.
func (r *Runner) commitMetrics(collected *metrics.Snapshot) error {
	now := r.now().UTC()
	return r.o.Store.Update(func(s *state.State) error {
		s.Counters = collected.Baseline
		s.LogOffsets = collected.LogOffsets
		s.LastSeen = collected.LastSeen
		s.LastMetricsAt = &now
		return nil
	})
}

// localXrayStatus builds the Xray status for heartbeat/metrics payloads.
func (r *Runner) localXrayStatus(ctx context.Context) api.XrayStatus {
	status, err := systemd.Show(ctx, r.execRunner(), r.o.Config.SystemctlBin, r.o.Config.XrayUnit)
	if err != nil {
		return api.XrayStatus{Active: false, Version: r.xrayVersion(ctx)}
	}
	bootSeconds := 0.0
	if r.o.ProcUptime != nil {
		if seconds, err := r.o.ProcUptime(); err == nil {
			bootSeconds = seconds
		}
	}
	return api.XrayStatus{
		Active:  status.Active(),
		UptimeS: systemd.UptimeSeconds(status, bootSeconds),
		Version: r.xrayVersion(ctx),
		PID:     status.MainPID,
	}
}

// unitState returns the ActiveState of a unit, or "unknown".
func (r *Runner) unitState(ctx context.Context, unit string) string {
	if unit == "" {
		return "unknown"
	}
	status, err := systemd.Show(ctx, r.execRunner(), r.o.Config.SystemctlBin, unit)
	if err != nil {
		return "unknown"
	}
	if status.ActiveState == "" {
		return "unknown"
	}
	return status.ActiveState
}

// degradedReason combines the apply, metrics and process degradation reasons.
func (r *Runner) degradedReason(xrayActive bool) string {
	r.mu.Lock()
	applyDegraded := r.applyDegraded
	r.mu.Unlock()
	var reasons []string
	if !xrayActive {
		reasons = append(reasons, "xray is not active")
	}
	if applyDegraded != "" {
		reasons = append(reasons, applyDegraded)
	}
	if stored := r.o.Store.Snapshot().DegradedReason; stored != "" && applyDegraded == "" {
		reasons = append(reasons, stored)
	}
	return strings.Join(reasons, "; ")
}

// execRunner returns the command runner used for systemctl calls.
func (r *Runner) execRunner() execx.Runner {
	return r.o.Exec
}

// xrayVersion returns the cached Xray version.
func (r *Runner) xrayVersion(ctx context.Context) string {
	if r.o.Probe == nil {
		return ""
	}
	return r.o.Probe.Version(ctx)
}

// markAuthBlocked stops panel traffic for the configured auth backoff.
func (r *Runner) markAuthBlocked() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.authBlockedUntil = r.now().Add(r.authBackoff())
}

// clearAuthBlocked resets the auth backoff after a successful call.
func (r *Runner) clearAuthBlocked() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.authBlockedUntil = time.Time{}
}

// authBlocked reports whether the agent is in the auth backoff window.
func (r *Runner) authBlocked() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return !r.authBlockedUntil.IsZero() && r.now().Before(r.authBlockedUntil)
}

// unwrapJSONString accepts either a raw configuration object or a JSON string
// containing the configuration and returns the configuration bytes.
func unwrapJSONString(raw []byte) []byte {
	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) >= 2 && trimmed[0] == '"' {
		var encoded string
		if err := json.Unmarshal(trimmed, &encoded); err == nil && json.Valid([]byte(encoded)) {
			return []byte(encoded)
		}
	}
	return trimmed
}

// hostnameOrUnknown returns the hostname or a fallback.
func hostnameOrUnknown() (string, error) {
	hostname, err := os.Hostname()
	if err != nil || strings.TrimSpace(hostname) == "" {
		return "unknown", err
	}
	return hostname, nil
}
