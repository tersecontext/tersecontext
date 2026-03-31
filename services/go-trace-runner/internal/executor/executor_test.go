package executor

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLockFileCreatedDuringExecution(t *testing.T) {
	sessionDir := t.TempDir()
	exec := New(sessionDir)
	lockPath := filepath.Join(sessionDir, ".lock")

	exec.CreateLock()
	if _, err := os.Stat(lockPath); os.IsNotExist(err) {
		t.Error("lock file should exist during execution")
	}

	exec.ReleaseLock()
	if _, err := os.Stat(lockPath); !os.IsNotExist(err) {
		t.Error("lock file should be removed after execution")
	}
}

func TestTimeoutCalculation(t *testing.T) {
	exec := New(t.TempDir())

	timeout := exec.Timeout("test", 30)
	if timeout.Seconds() != 30 {
		t.Errorf("test timeout should be 30s, got %v", timeout)
	}

	timeout = exec.Timeout("server", 30)
	if timeout.Seconds() != 60 {
		t.Errorf("server timeout should be 60s, got %v", timeout)
	}
}
