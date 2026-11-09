package metrics

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"vpn-agent/internal/api"
)

// DefaultBufferTTL is how long metrics stay buffered while the panel is
// unreachable.
const DefaultBufferTTL = 24 * time.Hour

// DefaultBufferMaxBytes bounds the buffer file size.
const DefaultBufferMaxBytes int64 = 32 << 20

// Buffer is the append-only JSONL file of metrics batches collected while the
// panel was unreachable. It is replayed with "replay": true and trimmed to the
// configured TTL.
type Buffer struct {
	Path     string
	TTL      time.Duration
	MaxBytes int64
	// Now is injectable for tests.
	Now func() time.Time

	appends int
}

// NewBuffer returns a metrics buffer at path.
func NewBuffer(path string, ttl time.Duration) *Buffer {
	if ttl <= 0 {
		ttl = DefaultBufferTTL
	}
	return &Buffer{Path: path, TTL: ttl, MaxBytes: DefaultBufferMaxBytes}
}

// now returns the current UTC time or the test override.
func (b *Buffer) now() time.Time {
	if b.Now != nil {
		return b.Now().UTC()
	}
	return time.Now().UTC()
}

// Append durably appends one batch to the buffer. It periodically trims stale
// entries so the file never grows beyond the TTL window for long.
func (b *Buffer) Append(batch *api.MetricsBatch) error {
	if batch == nil {
		return errors.New("metrics: cannot buffer a nil batch")
	}
	if b.Path == "" {
		return errors.New("metrics: buffer path is empty")
	}
	if err := os.MkdirAll(filepath.Dir(b.Path), 0o700); err != nil {
		return fmt.Errorf("metrics: create buffer dir: %w", err)
	}
	data, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("metrics: marshal buffered batch: %w", err)
	}
	f, err := os.OpenFile(b.Path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return fmt.Errorf("metrics: open buffer: %w", err)
	}
	if _, err := f.Write(append(data, '\n')); err != nil {
		_ = f.Close()
		return fmt.Errorf("metrics: append buffer: %w", err)
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		return fmt.Errorf("metrics: fsync buffer: %w", err)
	}
	if err := f.Close(); err != nil {
		return fmt.Errorf("metrics: close buffer: %w", err)
	}
	b.appends++
	if b.appends%10 == 0 {
		_, _ = b.Trim()
		return nil
	}
	if b.MaxBytes > 0 {
		if fi, err := os.Stat(b.Path); err == nil && fi.Size() > b.MaxBytes {
			_, _ = b.Trim()
		}
	}
	return nil
}

// ReadAll returns every readable batch in append order. Corrupt lines are
// skipped and counted.
func (b *Buffer) ReadAll() ([]api.MetricsBatch, int, error) {
	f, err := os.Open(b.Path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, 0, nil
		}
		return nil, 0, fmt.Errorf("metrics: read buffer: %w", err)
	}
	defer func() { _ = f.Close() }()
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	var (
		batches []api.MetricsBatch
		skipped int
	)
	for scanner.Scan() {
		line := bytes.TrimSpace(scanner.Bytes())
		if len(line) == 0 {
			continue
		}
		var batch api.MetricsBatch
		if err := json.Unmarshal(line, &batch); err != nil {
			skipped++
			continue
		}
		batches = append(batches, batch)
	}
	if err := scanner.Err(); err != nil {
		return batches, skipped, fmt.Errorf("metrics: scan buffer: %w", err)
	}
	return batches, skipped, nil
}

// Len returns the number of buffered batches.
func (b *Buffer) Len() (int, error) {
	batches, _, err := b.ReadAll()
	return len(batches), err
}

// Trim drops batches older than the TTL and rewrites the file when anything
// changed. It returns the number of dropped batches.
func (b *Buffer) Trim() (int, error) {
	batches, skipped, err := b.ReadAll()
	if err != nil {
		return 0, err
	}
	now := b.now()
	kept := make([]api.MetricsBatch, 0, len(batches))
	removed := 0
	for _, batch := range batches {
		if batch.TS.IsZero() {
			removed++
			continue
		}
		if b.TTL > 0 && now.Sub(batch.TS.UTC()) > b.TTL {
			removed++
			continue
		}
		kept = append(kept, batch)
	}
	if removed == 0 && skipped == 0 {
		return 0, nil
	}
	if err := b.writeBatches(kept); err != nil {
		return removed, err
	}
	return removed, nil
}

// Replay sends up to limit buffered batches in order, marking each as replay.
// On a send error the unsent batches stay in the file (the failed one included)
// so the next attempt retries them.
func (b *Buffer) Replay(ctx context.Context, send func(ctx context.Context, batch *api.MetricsBatch) error, limit int) (replayed int, remaining int, err error) {
	batches, skipped, err := b.ReadAll()
	if err != nil {
		return 0, 0, err
	}
	if len(batches) == 0 {
		if skipped > 0 {
			_ = b.writeBatches(nil)
		}
		return 0, 0, nil
	}
	toSend := len(batches)
	if limit > 0 && toSend > limit {
		toSend = limit
	}
	sent := 0
	for sent < toSend {
		batch := batches[sent]
		batch.Replay = true
		if err := send(ctx, &batch); err != nil {
			remaining = len(batches) - sent
			_ = b.writeBatches(batches[sent:])
			return sent, remaining, err
		}
		sent++
	}
	remaining = len(batches) - sent
	if remaining == 0 {
		if err := b.writeBatches(nil); err != nil {
			return sent, 0, err
		}
		return sent, 0, nil
	}
	if err := b.writeBatches(batches[sent:]); err != nil {
		return sent, remaining, err
	}
	return sent, remaining, nil
}

// Clear removes the buffer file.
func (b *Buffer) Clear() error {
	if b.Path == "" {
		return nil
	}
	if err := os.Remove(b.Path); err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("metrics: clear buffer: %w", err)
	}
	return nil
}

// writeBatches atomically replaces the buffer file with the given batches.
// An empty slice removes the file.
func (b *Buffer) writeBatches(batches []api.MetricsBatch) error {
	if len(batches) == 0 {
		return b.Clear()
	}
	dir := filepath.Dir(b.Path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("metrics: create buffer dir: %w", err)
	}
	tmp, err := os.CreateTemp(dir, ".metrics-buffer-*.tmp")
	if err != nil {
		return fmt.Errorf("metrics: create temp buffer: %w", err)
	}
	tmpName := tmp.Name()
	defer func() { _ = os.Remove(tmpName) }()
	if err := tmp.Chmod(0o600); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("metrics: chmod temp buffer: %w", err)
	}
	writer := bufio.NewWriter(tmp)
	for _, batch := range batches {
		data, err := json.Marshal(batch)
		if err != nil {
			_ = tmp.Close()
			return fmt.Errorf("metrics: marshal buffered batch: %w", err)
		}
		if _, err := writer.Write(append(data, '\n')); err != nil {
			_ = tmp.Close()
			return fmt.Errorf("metrics: write temp buffer: %w", err)
		}
	}
	if err := writer.Flush(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("metrics: flush temp buffer: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("metrics: fsync temp buffer: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("metrics: close temp buffer: %w", err)
	}
	if err := os.Rename(tmpName, b.Path); err != nil {
		return fmt.Errorf("metrics: replace buffer: %w", err)
	}
	return nil
}
