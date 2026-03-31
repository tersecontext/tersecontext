package handlers

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/builder"
	"github.com/tersecontext/tc/services/go-instrumenter/internal/rewriter"
	"github.com/tersecontext/tc/services/go-instrumenter/internal/session"
)

// InstrumentRequest is the JSON body for POST /instrument.
type InstrumentRequest struct {
	Repo             string   `json:"repo"`
	RepoPath         string   `json:"repo_path"`
	CommitSHA        string   `json:"commit_sha"`
	Entrypoints      []string `json:"entrypoints"`
	Language         string   `json:"language"`
	BoundaryPatterns []string `json:"boundary_patterns"`
	IncludeDeps      []string `json:"include_deps"`
	TracertPath      string   `json:"tracert_path"`
}

// InstrumentResponse is the JSON body returned on success.
type InstrumentResponse struct {
	SessionID  string      `json:"session_id"`
	BinaryPath string      `json:"binary_path,omitempty"`
	Stats      interface{} `json:"stats,omitempty"`
	Status     string      `json:"status"`
}

// Instrument returns an http.HandlerFunc that handles POST /instrument.
func Instrument(sessMgr *session.Manager) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		var req InstrumentRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{"error": "invalid JSON body"})
			return
		}

		// Validate required fields.
		if req.Repo == "" || req.RepoPath == "" || req.CommitSHA == "" || len(req.Entrypoints) == 0 {
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]string{
				"error": "missing required fields: repo, repo_path, commit_sha, entrypoints",
			})
			return
		}

		// Create session.
		sess, err := sessMgr.Create()
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": "failed to create session"})
			return
		}

		// Copy source repo into session directory.
		destDir := filepath.Join(sess.Dir, "src")
		if err := copyDir(req.RepoPath, destDir); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{
				"error": fmt.Sprintf("failed to copy repo: %v", err),
			})
			return
		}

		// Run AST rewriter on copied source.
		matcher := rewriter.NewPatternMatcher(req.BoundaryPatterns)
		rw := rewriter.New(req.Repo, matcher)
		stats, err := rw.RewriteDir(destDir)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{
				"error": fmt.Sprintf("rewriter failed: %v", err),
			})
			return
		}

		// Resolve tracert path — prefer request field, fall back to env.
		tracertPath := req.TracertPath
		if tracertPath == "" {
			tracertPath = os.Getenv("TRACERT_PATH")
		}

		// If TRACERT_PATH is not set, skip the build step and return a partial response.
		if tracertPath == "" {
			log.Printf("TRACERT_PATH not set — skipping build for session %s", sess.ID)
			json.NewEncoder(w).Encode(InstrumentResponse{
				SessionID: sess.ID,
				Stats:     stats,
				Status:    "ready_no_build",
			})
			return
		}

		// Inject tracert dependency into go.mod.
		bldr := builder.New(tracertPath)
		if err := bldr.InjectTracertDep(destDir); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{
				"error": fmt.Sprintf("inject tracert dep failed: %v", err),
			})
			return
		}

		// Build binary.  Use test binary if all entrypoints start with "Test".
		binaryPath := filepath.Join(sess.Dir, "instrumented")
		allTests := allTestEntrypoints(req.Entrypoints)

		if allTests {
			err = bldr.BuildTestBinary(destDir, binaryPath, "./...")
		} else {
			err = bldr.BuildBinary(destDir, binaryPath, "./...")
		}
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{
				"error": fmt.Sprintf("build failed: %v", err),
			})
			return
		}

		json.NewEncoder(w).Encode(InstrumentResponse{
			SessionID:  sess.ID,
			BinaryPath: binaryPath,
			Stats:      stats,
			Status:     "ready",
		})
	}
}

// allTestEntrypoints returns true if every entrypoint name starts with "Test".
func allTestEntrypoints(eps []string) bool {
	for _, ep := range eps {
		if !strings.HasPrefix(ep, "Test") {
			return false
		}
	}
	return true
}

// copyDir recursively copies src into dst, creating dst if necessary.
func copyDir(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)

		if info.IsDir() {
			return os.MkdirAll(target, info.Mode())
		}

		return copyFile(path, target, info.Mode())
	})
}

// copyFile copies a single file from src to dst.
func copyFile(src, dst string, mode os.FileMode) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()

	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}

	out, err := os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, mode)
	if err != nil {
		return err
	}
	defer out.Close()

	_, err = io.Copy(out, in)
	return err
}
