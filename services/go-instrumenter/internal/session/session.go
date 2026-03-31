package session

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
)

type Session struct {
	ID        string
	Dir       string
	CreatedAt time.Time
}

type Manager struct {
	baseDir string
}

func NewManager(baseDir string) *Manager {
	os.MkdirAll(baseDir, 0755)
	return &Manager{baseDir: baseDir}
}

func (m *Manager) Create() (*Session, error) {
	id := uuid.New().String()
	dir := filepath.Join(m.baseDir, id)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("create session dir: %w", err)
	}
	return &Session{
		ID:        id,
		Dir:       dir,
		CreatedAt: time.Now(),
	}, nil
}

func (m *Manager) CleanupExpired(maxAge time.Duration) {
	entries, err := os.ReadDir(m.baseDir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		dir := filepath.Join(m.baseDir, entry.Name())

		// Skip locked sessions
		if _, err := os.Stat(filepath.Join(dir, ".lock")); err == nil {
			continue
		}

		info, err := entry.Info()
		if err != nil {
			continue
		}
		if maxAge > 0 && time.Since(info.ModTime()) < maxAge {
			continue
		}
		os.RemoveAll(dir)
	}
}
