package handlers_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/redis/go-redis/v9"
	"github.com/tersecontext/tc/services/api-gateway/internal/handlers"
)

// stubStream captures XAdd calls for inspection.
type stubStream struct {
	lastArgs *redis.XAddArgs
	err      error
}

func (s *stubStream) XAdd(_ context.Context, args *redis.XAddArgs) *redis.StringCmd {
	s.lastArgs = args
	cmd := redis.NewStringCmd(context.Background())
	if s.err != nil {
		cmd.SetErr(s.err)
	}
	return cmd
}

func TestIndex_Success_FullRescan(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	body := `{"repo_path":"/repos/acme","full_rescan":true}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	h.Handle(rr, req)

	if rr.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", rr.Code)
	}
	var resp map[string]string
	json.NewDecoder(rr.Body).Decode(&resp)
	if resp["job_id"] == "" {
		t.Fatal("job_id should be non-empty")
	}
	if resp["message"] != "indexing started" {
		t.Fatalf("unexpected message: %s", resp["message"])
	}
	if stub.lastArgs == nil {
		t.Fatal("XAdd should have been called")
	}
	if stub.lastArgs.Stream != "stream:file-changed" {
		t.Fatalf("wrong stream: %s", stub.lastArgs.Stream)
	}
	vals := stub.lastArgs.Values.(map[string]any)
	if vals["diff_type"] != "full_rescan" {
		t.Fatalf("expected diff_type full_rescan, got %v", vals["diff_type"])
	}
	if vals["repo_path"] != "/repos/acme" {
		t.Fatalf("wrong repo_path: %v", vals["repo_path"])
	}
	if vals["job_id"] != resp["job_id"] {
		t.Fatal("job_id in stream must match response")
	}
}

func TestIndex_Success_Incremental(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	body := `{"repo_path":"/repos/acme","full_rescan":false}`
	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(body))
	h.Handle(rr, req)

	if rr.Code != http.StatusAccepted {
		t.Fatalf("expected 202, got %d", rr.Code)
	}
	vals := stub.lastArgs.Values.(map[string]any)
	if vals["diff_type"] != "incremental" {
		t.Fatalf("expected incremental, got %v", vals["diff_type"])
	}
}

func TestIndex_MissingRepoPath(t *testing.T) {
	stub := &stubStream{}
	h := handlers.NewIndexHandler(stub)

	rr := httptest.NewRecorder()
	req := httptest.NewRequest("POST", "/index", bytes.NewBufferString(`{}`))
	h.Handle(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
}
