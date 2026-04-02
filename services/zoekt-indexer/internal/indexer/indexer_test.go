package indexer

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestIndexRepo_InvokesZoektIndex(t *testing.T) {
	// Write a fake zoekt-index that records its arguments.
	tmpDir := t.TempDir()
	fakeScript := filepath.Join(tmpDir, "zoekt-index")
	argsFile := filepath.Join(tmpDir, "args.txt")
	script := "#!/bin/sh\necho \"$@\" > " + argsFile + "\n"
	if err := os.WriteFile(fakeScript, []byte(script), 0755); err != nil {
		t.Fatal(err)
	}

	// Put fake binary first in PATH.
	origPath := os.Getenv("PATH")
	os.Setenv("PATH", tmpDir+":"+origPath)
	defer os.Setenv("PATH", origPath)

	idx := NewIndexer("/data/index", "/repos")
	if err := idx.IndexRepo("my-repo"); err != nil {
		t.Fatalf("IndexRepo returned error: %v", err)
	}

	got, err := os.ReadFile(argsFile)
	if err != nil {
		t.Fatalf("args file not written: %v", err)
	}
	// Expect: -index /data/index /repos/my-repo
	want := "-index /data/index /repos/my-repo\n"
	if string(got) != want {
		t.Errorf("zoekt-index called with %q, want %q", got, want)
	}
}

func TestIndexRepo_ErrorOnNonZeroExit(t *testing.T) {
	tmpDir := t.TempDir()
	fakeScript := filepath.Join(tmpDir, "zoekt-index")
	if err := os.WriteFile(fakeScript, []byte("#!/bin/sh\nexit 1\n"), 0755); err != nil {
		t.Fatal(err)
	}
	origPath := os.Getenv("PATH")
	os.Setenv("PATH", tmpDir+":"+origPath)
	defer os.Setenv("PATH", origPath)

	idx := NewIndexer("/data/index", "/repos")
	err := idx.IndexRepo("my-repo")
	if err == nil {
		t.Fatal("expected error on non-zero exit, got nil")
	}
	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) {
		t.Errorf("expected ExitError, got %T: %v", err, err)
	}
}
