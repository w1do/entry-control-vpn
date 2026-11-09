// Package state persists the agent state between restarts.
//
// State lives in <base>/state/state.json and is replaced atomically: a temp
// file in the same directory is written, fsynced and renamed over the target.
// A corrupted state file never prevents the node from booting: it is moved
// aside and the agent starts with a clean state.
package state

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Schema is the current state file schema version.
const Schema = 1

// Status values used by PendingApply.
const (
	ApplyInProgress = "in_progress"
	ApplyApplied    = "applied"
	ApplyFailed     = "failed"
)

// PendingApply is the apply result that still has to be reported to the panel.
// It makes /agent/v1/apply-result delivery at-least-once across agent restarts.
type PendingApply struct {
	Revision  int64     `json:"revision"`
	Status    string    `json:"status"`
	Error     string    `json:"error,omitempty"`
	LogTail   string    `json:"log_tail,omitempty"`
	SHA256    string    `json:"sha256,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

// LogOffsets stores how far the agent has consumed the xray logs.
type LogOffsets struct {
	Access int64 `json:"access"`
	Error  int64 `json:"error"`
}

// State is the durable agent state.
type State struct {
	Schema int `json:"schema"`

	Revision     int64      `json:"revision"`
	ConfigSHA256 string     `json:"config_sha256,omitempty"`
	AppliedAt    *time.Time `json:"applied_at,omitempty"`

	// Counters holds the last observed cumulative Xray counters. Only deltas
	// between two observations are reported to the panel.
	Counters map[string]int64 `json:"counters,omitempty"`

	LogOffsets LogOffsets `json:"log_offsets"`

	// LastSeen is the per-client last activity timestamp used as online
	// fallback when the Xray API cannot report online users.
	LastSeen map[string]time.Time `json:"last_seen,omitempty"`

	PendingApply *PendingApply `json:"pending_apply,omitempty"`

	LastRegisterAt  *time.Time `json:"last_register_at,omitempty"`
	LastHeartbeatAt *time.Time `json:"last_heartbeat_at,omitempty"`
	LastMetricsAt   *time.Time `json:"last_metrics_at,omitempty"`

	// DegradedReason is the last known reason why the node is not fully healthy.
	DegradedReason string `json:"degraded_reason,omitempty"`
}

// Store is a concurrency safe accessor for the state file.
type Store struct {
	path string

	mu    sync.Mutex
	state State

	// LoadWarning is non nil when the previous state file was unreadable and
	// had to be discarded.
	LoadWarning error
}

// Open loads the state at path, creating a default state when the file does
// not exist. A corrupt file is moved aside and the store starts clean.
func Open(path string) (*Store, error) {
	if path == "" {
		return nil, errors.New("state: empty path")
	}
	s := &Store{path: path, state: defaultState()}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		var loaded State
		if err := json.Unmarshal(raw, &loaded); err != nil {
			corrupt := path + ".corrupt"
			_ = os.Rename(path, corrupt)
			s.LoadWarning = fmt.Errorf("state: %s is corrupt, moved to %s: %w", path, corrupt, err)
			return s, nil
		}
		if loaded.Schema == 0 {
			loaded.Schema = Schema
		}
		if loaded.Schema > Schema {
			s.LoadWarning = fmt.Errorf("state: schema %d is newer than supported %d", loaded.Schema, Schema)
		}
		if loaded.Counters == nil {
			loaded.Counters = map[string]int64{}
		}
		if loaded.LastSeen == nil {
			loaded.LastSeen = map[string]time.Time{}
		}
		s.state = loaded
		return s, nil
	case errors.Is(err, os.ErrNotExist):
		return s, nil
	default:
		return nil, fmt.Errorf("state: read %s: %w", path, err)
	}
}

// Path returns the backing file path.
func (s *Store) Path() string { return s.path }

// Snapshot returns a deep copy of the current state.
func (s *Store) Snapshot() State {
	s.mu.Lock()
	defer s.mu.Unlock()
	return cloneState(s.state)
}

// Update atomically mutates the state through fn and persists it.
// If fn returns an error or persisting fails, the in-memory state is unchanged.
func (s *Store) Update(fn func(*State) error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	next := cloneState(s.state)
	if err := fn(&next); err != nil {
		return err
	}
	next.Schema = Schema
	if err := writeAtomic(s.path, next); err != nil {
		return err
	}
	s.state = next
	return nil
}

// Save persists the current state.
func (s *Store) Save() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return writeAtomic(s.path, s.state)
}

func defaultState() State {
	return State{
		Schema:   Schema,
		Counters: map[string]int64{},
		LastSeen: map[string]time.Time{},
	}
}

// cloneState deep copies the maps and pointers that can be mutated by callers.
func cloneState(in State) State {
	out := in
	if in.Counters != nil {
		out.Counters = make(map[string]int64, len(in.Counters))
		for k, v := range in.Counters {
			out.Counters[k] = v
		}
	} else {
		out.Counters = map[string]int64{}
	}
	if in.LastSeen != nil {
		out.LastSeen = make(map[string]time.Time, len(in.LastSeen))
		for k, v := range in.LastSeen {
			out.LastSeen[k] = v
		}
	} else {
		out.LastSeen = map[string]time.Time{}
	}
	if in.PendingApply != nil {
		p := *in.PendingApply
		out.PendingApply = &p
	}
	out.AppliedAt = cloneTime(in.AppliedAt)
	out.LastRegisterAt = cloneTime(in.LastRegisterAt)
	out.LastHeartbeatAt = cloneTime(in.LastHeartbeatAt)
	out.LastMetricsAt = cloneTime(in.LastMetricsAt)
	return out
}

func cloneTime(in *time.Time) *time.Time {
	if in == nil {
		return nil
	}
	t := *in
	return &t
}

// writeAtomic writes v as indented JSON to path via a temp file + rename.
func writeAtomic(path string, v any) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("state: create dir %s: %w", dir, err)
	}
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return fmt.Errorf("state: marshal: %w", err)
	}
	data = append(data, '\n')
	tmp, err := os.CreateTemp(dir, ".state-*.tmp")
	if err != nil {
		return fmt.Errorf("state: create temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("state: chmod temp file: %w", err)
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("state: write temp file: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("state: fsync temp file: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("state: close temp file: %w", err)
	}
	if err := os.Rename(tmpName, path); err != nil {
		return fmt.Errorf("state: rename temp file: %w", err)
	}
	if d, err := os.Open(dir); err == nil {
		_ = d.Sync()
		_ = d.Close()
	}
	return nil
}
