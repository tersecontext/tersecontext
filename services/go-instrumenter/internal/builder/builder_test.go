package builder

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInjectTestMain_WritesFile(t *testing.T) {
	dir := t.TempDir()
	pkgDir := filepath.Join(dir, "mypkg")
	os.MkdirAll(pkgDir, 0755)
	os.WriteFile(filepath.Join(pkgDir, "foo_test.go"), []byte("package mypkg\n\nfunc TestFoo(t *testing.T) {}\n"), 0644)

	b := New("/fake/tracert")
	if err := b.InjectTestMain(dir, "./mypkg"); err != nil {
		t.Fatal(err)
	}

	content, err := os.ReadFile(filepath.Join(pkgDir, "tracert_main_test.go"))
	if err != nil {
		t.Fatal("tracert_main_test.go not created:", err)
	}
	if !strings.Contains(string(content), "func TestMain(") {
		t.Error("missing TestMain in injected file")
	}
	if !strings.Contains(string(content), "tracert.Init") {
		t.Error("missing tracert.Init in injected file")
	}
	if !strings.Contains(string(content), "tracert.Flush") {
		t.Error("missing tracert.Flush in injected file")
	}
}

func TestInjectTestMain_SkipsExistingTestMain(t *testing.T) {
	dir := t.TempDir()
	pkgDir := filepath.Join(dir, "mypkg")
	os.MkdirAll(pkgDir, 0755)
	existing := "package mypkg\n\nimport \"testing\"\n\nfunc TestMain(m *testing.M) { m.Run() }\n"
	os.WriteFile(filepath.Join(pkgDir, "main_test.go"), []byte(existing), 0644)

	b := New("/fake/tracert")
	if err := b.InjectTestMain(dir, "./mypkg"); err != nil {
		t.Fatal(err)
	}

	if _, err := os.Stat(filepath.Join(pkgDir, "tracert_main_test.go")); err == nil {
		t.Error("should not have written tracert_main_test.go when TestMain already exists")
	}
}

func TestInjectTestMain_SkipsWildcard(t *testing.T) {
	b := New("/fake/tracert")
	if err := b.InjectTestMain(t.TempDir(), "./..."); err != nil {
		t.Error("wildcard should not return error, got:", err)
	}
}

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
