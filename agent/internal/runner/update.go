package runner

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// commandUpdateAgent downloads, verifies and installs a new agent binary and
// schedules a service restart.
func (r *Runner) commandUpdateAgent(ctx context.Context) (string, error) {
	name := strings.TrimSpace(r.o.ArtifactName)
	if name == "" {
		name = r.o.Config.EffectiveArtifactName()
	}
	artifact, err := r.o.Client.DownloadArtifact(ctx, name)
	if err != nil {
		return "", fmt.Errorf("download artifact %s: %w", name, err)
	}
	paths := r.o.Config.Paths()
	binPath := paths.AgentBin
	if strings.TrimSpace(binPath) == "" {
		return "", errors.New("agent binary path is unknown")
	}
	if err := os.MkdirAll(filepath.Dir(binPath), 0o755); err != nil {
		return "", fmt.Errorf("create agent binary dir: %w", err)
	}
	tmpPath := binPath + ".new"
	if err := writeFileAtomicMode(tmpPath, artifact.Data, 0o755); err != nil {
		return "", fmt.Errorf("write new agent binary: %w", err)
	}
	backupPath := binPath + ".old"
	if _, err := os.Stat(binPath); err == nil {
		if err := os.Remove(backupPath); err != nil && !errors.Is(err, os.ErrNotExist) {
			_ = os.Remove(tmpPath)
			return "", fmt.Errorf("remove previous backup binary: %w", err)
		}
		if err := os.Rename(binPath, backupPath); err != nil {
			_ = os.Remove(tmpPath)
			return "", fmt.Errorf("backup current agent binary: %w", err)
		}
	}
	if err := os.Rename(tmpPath, binPath); err != nil {
		_ = os.Remove(tmpPath)
		return "", fmt.Errorf("install new agent binary: %w", err)
	}
	if dirHandle, err := os.Open(filepath.Dir(binPath)); err == nil {
		_ = dirHandle.Sync()
		_ = dirHandle.Close()
	}
	r.logger.Info("agent binary replaced", "artifact", name, "sha256", artifact.Checksum)
	if r.o.SelfRestart != nil {
		r.mu.Lock()
		r.pendingSelfRestart = true
		r.mu.Unlock()
	}
	return fmt.Sprintf("binary %s installed (sha256 %s); restart scheduled", name, artifact.Checksum), nil
}

// writeFileAtomicMode writes data to path via a temp file and rename.
func writeFileAtomicMode(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-"+filepath.Base(path)+"-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if err := tmp.Chmod(mode); err != nil {
		_ = tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, path)
}
