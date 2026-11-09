package uninstall

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// RefusedError explains why an uninstall was refused.
type RefusedError struct {
	BaseDir string
	Reason  string
}

// Error implements error.
func (e *RefusedError) Error() string {
	return fmt.Sprintf("uninstall refused for base dir %q: %s", e.BaseDir, e.Reason)
}

// protectedDirs are never removed, even when passed as the base directory.
var protectedDirs = []string{
	"/", "/root", "/home", "/etc", "/usr", "/var", "/bin", "/sbin",
	"/lib", "/lib32", "/lib64", "/boot", "/opt", "/srv", "/mnt", "/media",
	"/proc", "/sys", "/dev", "/run", "/tmp", "/snap",
}

// GuardBase validates that baseDir is a real, non-symlinked agent state root
// inside the user home directory and returns its resolved real path.
func GuardBase(baseDir string) (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("uninstall: determine home directory: %w", err)
	}
	return GuardBaseWithHome(baseDir, home)
}

// GuardBaseWithHome is GuardBase with an explicit home directory (tests).
func GuardBaseWithHome(baseDir, homeDir string) (string, error) {
	refuse := func(reason string) (string, error) {
		return "", &RefusedError{BaseDir: baseDir, Reason: reason}
	}
	if strings.TrimSpace(baseDir) == "" {
		return refuse("empty path")
	}
	if strings.TrimSpace(homeDir) == "" {
		return refuse("home directory is unknown")
	}
	abs, err := filepath.Abs(baseDir)
	if err != nil {
		return refuse(fmt.Sprintf("resolve absolute path: %v", err))
	}
	clean := filepath.Clean(abs)
	if clean == string(filepath.Separator) {
		return refuse("refusing to delete the filesystem root")
	}
	fi, err := os.Lstat(clean)
	if err != nil {
		if os.IsNotExist(err) {
			return refuse("base directory does not exist")
		}
		return refuse(fmt.Sprintf("inspect base directory: %v", err))
	}
	if fi.Mode()&os.ModeSymlink != 0 {
		return refuse("base directory is a symlink")
	}
	if !fi.IsDir() {
		return refuse("base path is not a directory")
	}
	resolved, err := filepath.EvalSymlinks(clean)
	if err != nil {
		return refuse(fmt.Sprintf("resolve symlinks: %v", err))
	}
	resolved = filepath.Clean(resolved)

	homeAbs, err := filepath.Abs(homeDir)
	if err != nil {
		return refuse(fmt.Sprintf("resolve home directory: %v", err))
	}
	homeResolved := filepath.Clean(homeAbs)
	if evaluated, err := filepath.EvalSymlinks(homeResolved); err == nil {
		homeResolved = filepath.Clean(evaluated)
	}

	if resolved == homeResolved {
		return refuse("base directory is the home directory")
	}
	if isAncestor(resolved, homeResolved) {
		return refuse("base directory is an ancestor of the home directory")
	}
	for _, protected := range protectedDirs {
		if resolved == filepath.Clean(protected) {
			return refuse(fmt.Sprintf("%q is a protected system directory", protected))
		}
	}
	rel, err := filepath.Rel(homeResolved, resolved)
	if err != nil {
		return refuse(fmt.Sprintf("compare with home directory: %v", err))
	}
	if rel == "." || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return refuse("base directory is not inside the home directory")
	}
	parts := strings.Split(strings.Trim(resolved, string(filepath.Separator)), string(filepath.Separator))
	if len(parts) < 3 {
		return refuse("base directory is too close to the filesystem root")
	}
	marker := filepath.Join(resolved, "etc", "agent.json")
	markerInfo, err := os.Lstat(marker)
	if err != nil {
		return refuse("does not look like an agent base directory (missing etc/agent.json)")
	}
	if !markerInfo.Mode().IsRegular() {
		return refuse("does not look like an agent base directory (etc/agent.json is not a regular file)")
	}
	return resolved, nil
}

// isAncestor reports whether parent contains child.
func isAncestor(parent, child string) bool {
	if parent == child {
		return false
	}
	rel, err := filepath.Rel(parent, child)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
