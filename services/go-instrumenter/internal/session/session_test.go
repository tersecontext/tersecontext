package session

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCreateSession(t *testing.T) {
	baseDir := t.TempDir()
	mgr := NewManager(baseDir)

	sess, err := mgr.Create()
	if err != nil {
		t.Fatal(err)
	}
	if sess.ID == "" {
		t.Error("session ID should not be empty")
	}
	if _, err := os.Stat(sess.Dir); os.IsNotExist(err) {
		t.Error("session directory should exist")
	}
}

func TestCleanupRespectsLockFiles(t *testing.T) {
	baseDir := t.TempDir()
	mgr := NewManager(baseDir)

	sess, _ := mgr.Create()

	// Create a lock file
	lockPath := filepath.Join(sess.Dir, ".lock")
	os.WriteFile(lockPath, []byte("locked"), 0644)

	// Cleanup should skip locked sessions
	mgr.CleanupExpired(0) // 0 = cleanup everything expired

	if _, err := os.Stat(sess.Dir); os.IsNotExist(err) {
		t.Error("locked session should not be cleaned up")
	}

	// Remove lock, cleanup should now work
	os.Remove(lockPath)
	mgr.CleanupExpired(0)

	if _, err := os.Stat(sess.Dir); !os.IsNotExist(err) {
		t.Error("unlocked session should be cleaned up")
	}
}
