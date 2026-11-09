package state

import (
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestOpenMissingUsesDefaults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state", "state.json")
	store, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	snap := store.Snapshot()
	if snap.Schema != Schema {
		t.Errorf("schema = %d", snap.Schema)
	}
	if snap.Counters == nil || snap.LastSeen == nil {
		t.Errorf("maps must not be nil")
	}
	if store.LoadWarning != nil {
		t.Errorf("unexpected warning: %v", store.LoadWarning)
	}
}

func TestUpdatePersists(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state", "state.json")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Update(func(s *State) error {
		s.Revision = 42
		s.ConfigSHA256 = "abc"
		s.Counters = map[string]int64{"user>>>a@b>>>traffic>>>uplink": 10}
		s.LogOffsets = LogOffsets{Access: 128, Error: 64}
		return nil
	}); err != nil {
		t.Fatalf("Update: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("state mode = %04o, want 0600", mode)
	}
	reopened, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	snap := reopened.Snapshot()
	if snap.Revision != 42 || snap.ConfigSHA256 != "abc" {
		t.Errorf("persisted revision = %d sha = %q", snap.Revision, snap.ConfigSHA256)
	}
	if snap.Counters["user>>>a@b>>>traffic>>>uplink"] != 10 {
		t.Errorf("persisted counters = %v", snap.Counters)
	}
	if snap.LogOffsets.Access != 128 || snap.LogOffsets.Error != 64 {
		t.Errorf("persisted offsets = %+v", snap.LogOffsets)
	}
}

func TestSnapshotIsDeepCopy(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Update(func(s *State) error {
		s.Counters["x"] = 1
		s.LastSeen["a@b"] = time.Now().UTC()
		s.PendingApply = &PendingApply{Revision: 1, Status: ApplyApplied}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	snap := store.Snapshot()
	snap.Counters["x"] = 999
	snap.LastSeen["c@d"] = time.Now()
	snap.PendingApply.Status = ApplyFailed
	again := store.Snapshot()
	if again.Counters["x"] != 1 {
		t.Errorf("counter was mutated through a snapshot")
	}
	if _, ok := again.LastSeen["c@d"]; ok {
		t.Errorf("last-seen was mutated through a snapshot")
	}
	if again.PendingApply.Status != ApplyApplied {
		t.Errorf("pending apply was mutated through a snapshot")
	}
}

func TestUpdateErrorKeepsState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Update(func(s *State) error {
		s.Revision = 5
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	wantErr := os.ErrInvalid
	if err := store.Update(func(s *State) error {
		s.Revision = 99
		return wantErr
	}); err == nil {
		t.Fatalf("expected an error")
	}
	if got := store.Snapshot().Revision; got != 5 {
		t.Fatalf("revision = %d, want 5", got)
	}
}

func TestCorruptStateIsMovedAside(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if store.LoadWarning == nil {
		t.Fatalf("expected a load warning")
	}
	if _, err := os.Stat(path + ".corrupt"); err != nil {
		t.Fatalf("corrupt file was not preserved: %v", err)
	}
	if got := store.Snapshot().Revision; got != 0 {
		t.Fatalf("revision = %d", got)
	}
}

func TestConcurrentUpdates(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for j := 0; j < 10; j++ {
				if err := store.Update(func(s *State) error {
					s.Revision++
					return nil
				}); err != nil {
					t.Errorf("Update: %v", err)
					return
				}
			}
		}()
	}
	wg.Wait()
	if got := store.Snapshot().Revision; got != 100 {
		t.Fatalf("revision = %d, want 100", got)
	}
}
