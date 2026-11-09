package metrics

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"vpn-agent/internal/api"
)

func testBatch(ts time.Time, up int64) *api.MetricsBatch {
	return &api.MetricsBatch{
		TS:           ts.UTC(),
		Revision:     3,
		TrafficDelta: api.Traffic{Up: up},
		Xray:         api.XrayStatus{Active: true},
	}
}

func TestBufferAppendReadTrim(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state", "metrics-buffer.jsonl")
	now := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	buffer := NewBuffer(path, time.Hour)
	buffer.Now = func() time.Time { return now }
	if err := buffer.Append(testBatch(now.Add(-2*time.Hour), 1)); err != nil {
		t.Fatalf("Append old: %v", err)
	}
	if err := buffer.Append(testBatch(now, 2)); err != nil {
		t.Fatalf("Append fresh: %v", err)
	}
	batches, skipped, err := buffer.ReadAll()
	if err != nil || skipped != 0 || len(batches) != 2 {
		t.Fatalf("ReadAll: %d %d %v", len(batches), skipped, err)
	}
	if batches[0].TrafficDelta.Up != 1 || batches[1].TrafficDelta.Up != 2 {
		t.Fatalf("order = %+v", batches)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if mode := info.Mode().Perm(); mode != 0o600 {
		t.Errorf("buffer mode = %04o", mode)
	}
	removed, err := buffer.Trim()
	if err != nil || removed != 1 {
		t.Fatalf("Trim = %d, %v", removed, err)
	}
	if count, err := buffer.Len(); err != nil || count != 1 {
		t.Fatalf("Len after trim = %d, %v", count, err)
	}
}

func TestBufferReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "buffer.jsonl")
	now := time.Now().UTC()
	buffer := NewBuffer(path, DefaultBufferTTL)
	for i := int64(1); i <= 3; i++ {
		if err := buffer.Append(testBatch(now, i)); err != nil {
			t.Fatal(err)
		}
	}
	var replayed []int64
	flags := map[int64]bool{}
	sent, remaining, err := buffer.Replay(context.Background(), func(_ context.Context, batch *api.MetricsBatch) error {
		replayed = append(replayed, batch.TrafficDelta.Up)
		flags[batch.TrafficDelta.Up] = batch.Replay
		return nil
	}, 0)
	if err != nil || sent != 3 || remaining != 0 {
		t.Fatalf("Replay = %d, %d, %v", sent, remaining, err)
	}
	if len(replayed) != 3 || replayed[0] != 1 || replayed[2] != 3 {
		t.Fatalf("replay order = %v", replayed)
	}
	for up, marked := range flags {
		if !marked {
			t.Errorf("batch %d was replayed without replay=true", up)
		}
	}
	if count, err := buffer.Len(); err != nil || count != 0 {
		t.Fatalf("buffer not cleared: %d, %v", count, err)
	}
}

func TestBufferReplayFailureKeepsRemaining(t *testing.T) {
	path := filepath.Join(t.TempDir(), "buffer.jsonl")
	buffer := NewBuffer(path, DefaultBufferTTL)
	now := time.Now().UTC()
	for i := int64(1); i <= 3; i++ {
		if err := buffer.Append(testBatch(now, i)); err != nil {
			t.Fatal(err)
		}
	}
	boom := errors.New("panel offline")
	sent, remaining, err := buffer.Replay(context.Background(), func(_ context.Context, batch *api.MetricsBatch) error {
		if batch.TrafficDelta.Up == 2 {
			return boom
		}
		return nil
	}, 0)
	if !errors.Is(err, boom) {
		t.Fatalf("error = %v", err)
	}
	if sent != 1 || remaining != 2 {
		t.Fatalf("Replay = %d sent, %d remaining", sent, remaining)
	}
	batches, _, err := buffer.ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	if len(batches) != 2 || batches[0].TrafficDelta.Up != 2 || batches[1].TrafficDelta.Up != 3 {
		t.Fatalf("remaining batches = %+v", batches)
	}
}

func TestBufferReplayLimit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "buffer.jsonl")
	buffer := NewBuffer(path, DefaultBufferTTL)
	now := time.Now().UTC()
	for i := int64(1); i <= 5; i++ {
		if err := buffer.Append(testBatch(now, i)); err != nil {
			t.Fatal(err)
		}
	}
	sent, remaining, err := buffer.Replay(context.Background(), func(context.Context, *api.MetricsBatch) error { return nil }, 2)
	if err != nil || sent != 2 || remaining != 3 {
		t.Fatalf("Replay = %d, %d, %v", sent, remaining, err)
	}
	if count, _ := buffer.Len(); count != 3 {
		t.Fatalf("buffer = %d", count)
	}
}

func TestBufferSkipsCorruptLines(t *testing.T) {
	path := filepath.Join(t.TempDir(), "buffer.jsonl")
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	batch := testBatch(time.Now().UTC(), 1)
	data, _ := json.Marshal(batch)
	content := append(data, '\n')
	content = append(content, []byte("this is not json\n")...)
	if err := os.WriteFile(path, content, 0o600); err != nil {
		t.Fatal(err)
	}
	buffer := NewBuffer(path, DefaultBufferTTL)
	batches, skipped, err := buffer.ReadAll()
	if err != nil || skipped != 1 || len(batches) != 1 {
		t.Fatalf("ReadAll = %d batches, %d skipped, %v", len(batches), skipped, err)
	}
	if _, err := buffer.Trim(); err != nil {
		t.Fatalf("Trim: %v", err)
	}
	readBack, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(readBack) == "" || strings.Contains(string(readBack), "this is not json") {
		t.Fatalf("corrupt line survived Trim: %q", readBack)
	}
}

func TestBufferMissingFile(t *testing.T) {
	buffer := NewBuffer(filepath.Join(t.TempDir(), "missing.jsonl"), DefaultBufferTTL)
	if count, err := buffer.Len(); err != nil || count != 0 {
		t.Fatalf("Len = %d, %v", count, err)
	}
	sent, remaining, err := buffer.Replay(context.Background(), func(context.Context, *api.MetricsBatch) error { return nil }, 0)
	if err != nil || sent != 0 || remaining != 0 {
		t.Fatalf("Replay = %d, %d, %v", sent, remaining, err)
	}
}
