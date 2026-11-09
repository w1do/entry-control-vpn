package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"vpn-agent/internal/api"
	"vpn-agent/internal/state"
)

// commandPayload is the generic payload of a panel command.
type commandPayload struct {
	Units []string `json:"units,omitempty"`
}

// HandleCommand executes one panel command and reports its result through
// /agent/v1/command-result. It returns ErrUninstallComplete when the node was
// uninstalled and the process must exit.
func (r *Runner) HandleCommand(ctx context.Context, command api.Command) error {
	if strings.TrimSpace(command.ID) == "" {
		return errors.New("runner: command id is empty")
	}
	r.logger.Info("executing panel command", "command_id", command.ID, "type", command.Type)
	output, err := r.executeCommand(ctx, command)
	status := api.CommandDone
	if err != nil {
		status = api.CommandError
		if output == "" {
			output = err.Error()
		} else {
			output += ": " + err.Error()
		}
	}
	if postErr := r.postCommandResult(ctx, command.ID, status, output); postErr != nil {
		r.logger.Warn("could not report command result", "command_id", command.ID, "error", postErr)
	}

	r.mu.Lock()
	uninstalled := r.uninstallComplete
	selfRestart := r.pendingSelfRestart
	selfStop := r.pendingSelfStop
	r.pendingSelfRestart = false
	r.pendingSelfStop = false
	r.mu.Unlock()
	if selfStop && r.o.StopSelf != nil {
		if stopErr := r.o.StopSelf(ctx); stopErr != nil {
			r.logger.Warn("could not stop the agent unit after uninstall", "error", stopErr)
		}
	}
	if selfRestart && r.o.SelfRestart != nil {
		if restartErr := r.o.SelfRestart(ctx); restartErr != nil {
			r.logger.Warn("scheduled agent self-restart failed", "error", restartErr)
		}
	}
	if uninstalled {
		return ErrUninstallComplete
	}
	return err
}

// executeCommand dispatches a command by type.
func (r *Runner) executeCommand(ctx context.Context, command api.Command) (string, error) {
	switch strings.ToLower(strings.TrimSpace(command.Type)) {
	case api.CommandReapply:
		return r.commandReapply(ctx)
	case api.CommandRestartService:
		return r.commandRestartServices(ctx, command.Payload)
	case api.CommandDiagnose:
		return r.commandDiagnose(ctx)
	case api.CommandUninstall:
		return r.commandUninstall(ctx)
	case api.CommandUpdateAgent:
		return r.commandUpdateAgent(ctx)
	default:
		return "", fmt.Errorf("unsupported command type %q", command.Type)
	}
}

// commandReapply forces a fresh configuration download and apply.
func (r *Runner) commandReapply(ctx context.Context) (string, error) {
	r.mu.Lock()
	r.forceReapply = true
	r.mu.Unlock()
	if err := r.ConfigPollOnce(ctx); err != nil {
		return "", err
	}
	snap := r.o.Store.Snapshot()
	if snap.PendingApply != nil && snap.PendingApply.Status == state.ApplyFailed {
		return "", fmt.Errorf("apply failed: %s", snap.PendingApply.Error)
	}
	return fmt.Sprintf("configuration revision %d re-applied", snap.Revision), nil
}

// commandRestartServices restarts Xray (and optionally the agent itself).
func (r *Runner) commandRestartServices(ctx context.Context, payload json.RawMessage) (string, error) {
	var requested commandPayload
	if len(payload) > 0 {
		_ = json.Unmarshal(payload, &requested)
	}
	units := requested.Units
	if len(units) == 0 {
		units = []string{"xray"}
	}
	var (
		results     []string
		firstErr    error
		agentWanted bool
	)
	for _, unit := range units {
		switch strings.ToLower(strings.TrimSpace(unit)) {
		case "xray", "xray.service", strings.ToLower(r.o.Config.XrayUnit):
			if err := r.o.Applier.Restart(ctx); err != nil {
				results = append(results, "xray: "+err.Error())
				if firstErr == nil {
					firstErr = err
				}
			} else {
				results = append(results, "xray: restarted and healthy")
			}
		case "agent", "vpn-agent", "vpn-agent.service", strings.ToLower(r.o.Config.AgentUnit):
			agentWanted = true
			results = append(results, "agent: restart scheduled after result reporting")
		default:
			results = append(results, "unknown unit: "+unit)
		}
	}
	if agentWanted {
		r.mu.Lock()
		r.pendingSelfRestart = true
		r.mu.Unlock()
	}
	return strings.Join(results, "; "), firstErr
}

// commandDiagnose collects a diagnostic report and POSTs it to the panel.
func (r *Runner) commandDiagnose(ctx context.Context) (string, error) {
	report, err := r.Diagnose(ctx)
	if err != nil {
		return "", err
	}
	if err := r.o.Client.PostDiagnose(ctx, report); err != nil {
		return "", fmt.Errorf("post diagnose report: %w", err)
	}
	return "diagnose report sent", nil
}

// commandUninstall removes the node and schedules process shutdown.
func (r *Runner) commandUninstall(ctx context.Context) (string, error) {
	report, err := r.o.Uninstall(ctx)
	if err != nil {
		return "", err
	}
	output, marshalErr := json.Marshal(report)
	if marshalErr != nil {
		output = []byte("uninstall report unavailable: " + marshalErr.Error())
	}
	if postErr := r.o.Client.PostUninstall(ctx, api.UninstallStatus); postErr != nil {
		r.logger.Warn("could not report uninstall completion", "error", postErr)
		output = append(output, []byte("; warning: could not report uninstall status: "+postErr.Error())...)
	}
	r.mu.Lock()
	r.uninstallComplete = true
	r.pendingSelfStop = true
	r.mu.Unlock()
	r.logger.Info("node uninstalled")
	return string(output), nil
}

// postCommandResult reports one command outcome to the panel.
func (r *Runner) postCommandResult(ctx context.Context, commandID, status, output string) error {
	const maxOutput = 4096
	if len(output) > maxOutput {
		output = output[:maxOutput] + "..."
	}
	return r.o.Client.PostCommandResult(ctx, &api.CommandResult{
		CommandID: commandID,
		Status:    status,
		Output:    output,
	})
}
