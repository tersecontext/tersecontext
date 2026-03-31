package builder

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestModEditAddsTracertDependency(t *testing.T) {
	dir := t.TempDir()
	gomod := "module example.com/test\n\ngo 1.24.0\n"
	os.WriteFile(filepath.Join(dir, "go.mod"), []byte(gomod), 0644)
	os.WriteFile(filepath.Join(dir, "main.go"), []byte("package main\nfunc main(){}\n"), 0644)

	b := New("/fake/tracert/path")
	err := b.InjectTracertDep(dir)
	if err != nil {
		t.Fatal(err)
	}

	content, _ := os.ReadFile(filepath.Join(dir, "go.mod"))
	if !strings.Contains(string(content), "tracert") {
		t.Error("go.mod should contain tracert dependency")
	}
}
