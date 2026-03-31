package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/session"
)

func TestInstrumentCreatesSession(t *testing.T) {
	repoDir := t.TempDir()
	os.WriteFile(filepath.Join(repoDir, "go.mod"), []byte("module example.com/test\n\ngo 1.24.0\n"), 0644)
	os.WriteFile(filepath.Join(repoDir, "main.go"), []byte("package main\n\nfunc main() {}\n"), 0644)

	sessMgr := session.NewManager(t.TempDir())
	handler := Instrument(sessMgr)

	body, _ := json.Marshal(map[string]any{
		"repo":              "test-repo",
		"repo_path":         repoDir,
		"commit_sha":        "abc123",
		"entrypoints":       []string{"TestLogin"},
		"language":          "go",
		"boundary_patterns": []string{},
		"include_deps":      []string{},
	})

	req := httptest.NewRequest("POST", "/instrument", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler(w, req)

	// The handler may return 200 or 500 depending on whether go build works
	// in test environment. At minimum verify it doesn't 400 (validation passed).
	if w.Code == http.StatusBadRequest {
		t.Fatalf("should not 400 with valid input: %s", w.Body.String())
	}

	// If 200, check session_id is present.
	if w.Code == http.StatusOK {
		var resp map[string]any
		json.Unmarshal(w.Body.Bytes(), &resp)
		if resp["session_id"] == nil || resp["session_id"] == "" {
			t.Error("response should contain session_id")
		}
	}
}

func TestInstrumentRejects400OnMissingFields(t *testing.T) {
	sessMgr := session.NewManager(t.TempDir())
	handler := Instrument(sessMgr)

	body, _ := json.Marshal(map[string]any{"repo": "test"})
	req := httptest.NewRequest("POST", "/instrument", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestInstrumentReturns200WithSessionID(t *testing.T) {
	// Create a minimal Go repo in a temp dir
	repoDir := t.TempDir()
	os.WriteFile(filepath.Join(repoDir, "go.mod"), []byte("module example.com/test\n\ngo 1.24.0\n"), 0644)
	os.WriteFile(filepath.Join(repoDir, "main.go"), []byte("package main\n\nfunc main() {}\n\nfunc TestLogin() {}\n"), 0644)

	sessMgr := session.NewManager(t.TempDir())
	handler := Instrument(sessMgr)

	body, _ := json.Marshal(map[string]any{
		"repo":              "test-repo",
		"repo_path":         repoDir,
		"commit_sha":        "abc123",
		"entrypoints":       []string{"TestLogin"},
		"language":          "go",
		"boundary_patterns": []string{"*.Handler*"},
		"include_deps":      []string{},
	})

	req := httptest.NewRequest("POST", "/instrument", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()

	handler(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}

	var resp map[string]any
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["session_id"] == nil || resp["session_id"] == "" {
		t.Error("response should contain session_id")
	}
}
