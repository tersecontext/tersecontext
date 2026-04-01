package builder

import (
	"fmt"
	"go/parser"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

type Builder struct {
	tracertPath string
}

func New(tracertPath string) *Builder {
	return &Builder{tracertPath: tracertPath}
}

func (b *Builder) InjectTracertDep(repoDir string) error {
	modPath := "github.com/tersecontext/tc/services/tracert"

	requireCmd := exec.Command("go", "mod", "edit",
		"-require="+modPath+"@v0.0.0")
	requireCmd.Dir = repoDir
	if out, err := requireCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go mod edit -require: %w\n%s", err, out)
	}

	replaceCmd := exec.Command("go", "mod", "edit",
		"-replace="+modPath+"="+b.tracertPath)
	replaceCmd.Dir = repoDir
	if out, err := replaceCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go mod edit -replace: %w\n%s", err, out)
	}

	tidyCmd := exec.Command("go", "mod", "tidy")
	tidyCmd.Dir = repoDir
	if out, err := tidyCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go mod tidy: %w\n%s", err, out)
	}

	return nil
}

func (b *Builder) BuildBinary(repoDir, outputPath, buildTarget string) error {
	cmd := exec.Command("go", "build", "-o", outputPath, buildTarget)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go build: %w\n%s", err, out)
	}
	return nil
}

func (b *Builder) BuildTestBinary(repoDir, outputPath, pkg string) error {
	if err := b.InjectTestMain(repoDir, pkg); err != nil {
		return fmt.Errorf("inject TestMain: %w", err)
	}
	cmd := exec.Command("go", "test", "-c", "-o", outputPath, pkg)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go test -c: %w\n%s", err, out)
	}
	return nil
}

// InjectTestMain writes a tracert_main_test.go into the package directory for
// pkg (e.g. "./internal/config") unless a TestMain already exists there.
// The injected TestMain calls tracert.Init from TRACERT_SOCKET and tracert.Flush
// after the test run so all buffered events are sent to the collector socket.
func (b *Builder) InjectTestMain(repoDir, pkg string) error {
	// Can't inject into a wildcard target — skip silently.
	if pkg == "./..." || pkg == "." || pkg == "" {
		return nil
	}

	pkgDir := filepath.Join(repoDir, filepath.FromSlash(strings.TrimPrefix(pkg, "./")))

	// Check whether any _test.go file in pkgDir already defines TestMain.
	entries, err := os.ReadDir(pkgDir)
	if err != nil {
		return fmt.Errorf("read pkg dir %s: %w", pkgDir, err)
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		src, _ := os.ReadFile(filepath.Join(pkgDir, e.Name()))
		if strings.Contains(string(src), "func TestMain(") {
			return nil // existing TestMain — leave it alone
		}
	}

	// Determine the package name from the first _test.go file (or any .go file).
	pkgName := inferPackageName(pkgDir)
	if pkgName == "" {
		return fmt.Errorf("could not infer package name in %s", pkgDir)
	}

	content := fmt.Sprintf(`package %s

import (
	"os"
	"testing"

	"github.com/tersecontext/tc/services/tracert"
)

// TestMain initialises tracert before the test run and flushes buffered events
// to the collector socket afterwards.
func TestMain(m *testing.M) {
	if sock := os.Getenv("TRACERT_SOCKET"); sock != "" {
		tracert.Init(sock)
	}
	code := m.Run()
	tracert.Flush()
	os.Exit(code)
}
`, pkgName)

	dest := filepath.Join(pkgDir, "tracert_main_test.go")
	return os.WriteFile(dest, []byte(content), 0644)
}

// inferPackageName returns the Go package name declared in any .go file in dir.
func inferPackageName(dir string) string {
	fset := token.NewFileSet()
	pkgs, err := parser.ParseDir(fset, dir, nil, parser.PackageClauseOnly)
	if err != nil {
		return ""
	}
	for name := range pkgs {
		// Prefer the non-test package name when both exist.
		if !strings.HasSuffix(name, "_test") {
			return name
		}
	}
	// Fall back to whatever we found (e.g. only foo_test package exists).
	for name := range pkgs {
		return strings.TrimSuffix(name, "_test")
	}
	return ""
}
