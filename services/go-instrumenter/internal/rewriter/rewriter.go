package rewriter

import (
	"bytes"
	"fmt"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strings"

	"github.com/tersecontext/tc/services/go-instrumenter/internal/rewriter/passes"
)

const tracertImport = "github.com/tersecontext/tc/services/tracert"

// RewriteStats holds counts from a directory rewrite operation.
type RewriteStats struct {
	Instrumented     int
	Skipped          int
	CoverageFiltered int
}

// Rewriter transforms Go source files by injecting tracing calls.
type Rewriter struct {
	repo    string
	matcher *PatternMatcher
}

// New creates a new Rewriter.
func New(repo string, matcher *PatternMatcher) *Rewriter {
	return &Rewriter{repo: repo, matcher: matcher}
}

// RewriteSource parses the given source, injects tracing, and returns the modified source.
func (rw *Rewriter) RewriteSource(filename string, src []byte) ([]byte, error) {
	if isGeneratedFile(src) {
		return src, nil
	}

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, filename, src, parser.ParseComments)
	if err != nil {
		return nil, fmt.Errorf("parse %s: %w", filename, err)
	}

	pkgName := file.Name.Name

	// First pass: instrument all FuncDecls
	ast.Inspect(file, func(n ast.Node) bool {
		fd, ok := n.(*ast.FuncDecl)
		if !ok || fd.Body == nil {
			return true
		}

		funcID := buildFuncID(rw.repo, pkgName, fd)
		shortName := shortFuncName(pkgName, fd)
		isBoundary := rw.matcher != nil && rw.matcher.IsBoundary(shortName)

		passes.InjectEntry(fd, funcID, isBoundary)
		return true
	})

	// Second pass: replace GoStmt nodes with tracert.Go(...) calls
	passes.WrapGoroutines(file)

	// Add the tracert import
	passes.EnsureImport(fset, file, tracertImport)

	// Format the result
	var buf bytes.Buffer
	if err := format.Node(&buf, fset, file); err != nil {
		return nil, fmt.Errorf("format %s: %w", filename, err)
	}

	return buf.Bytes(), nil
}

// RewriteDir rewrites all .go files in dir (skipping _test.go, cgo_, and generated files).
func (rw *Rewriter) RewriteDir(dir string) (RewriteStats, error) {
	var stats RewriteStats

	err := filepath.Walk(dir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if info.IsDir() {
			return nil
		}
		if !strings.HasSuffix(path, ".go") {
			return nil
		}

		base := filepath.Base(path)
		// Skip test files and cgo files
		if strings.HasSuffix(base, "_test.go") || strings.HasPrefix(base, "cgo_") {
			stats.Skipped++
			return nil
		}

		src, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("read %s: %w", path, err)
		}

		if isGeneratedFile(src) {
			stats.CoverageFiltered++
			return nil
		}

		result, err := rw.RewriteSource(path, src)
		if err != nil {
			return fmt.Errorf("rewrite %s: %w", path, err)
		}

		if err := os.WriteFile(path, result, info.Mode()); err != nil {
			return fmt.Errorf("write %s: %w", path, err)
		}

		stats.Instrumented++
		return nil
	})

	return stats, err
}

// isGeneratedFile returns true if the source contains a "Code generated" marker
// in the first 5 lines.
func isGeneratedFile(src []byte) bool {
	lines := bytes.SplitN(src, []byte("\n"), 6)
	limit := 5
	if len(lines) < limit {
		limit = len(lines)
	}
	for i := 0; i < limit; i++ {
		if bytes.Contains(lines[i], []byte("Code generated")) {
			return true
		}
	}
	return false
}

// buildFuncID constructs the fully qualified function ID:
// "{repo}/{pkg}.{ReceiverType}.{MethodName}" or "{repo}/{pkg}.{FuncName}"
func buildFuncID(repo, pkg string, fd *ast.FuncDecl) string {
	name := fd.Name.Name
	if fd.Recv != nil && len(fd.Recv.List) > 0 {
		recv := receiverTypeName(fd.Recv.List[0].Type)
		return fmt.Sprintf("%s/%s.%s.%s", repo, pkg, recv, name)
	}
	return fmt.Sprintf("%s/%s.%s", repo, pkg, name)
}

// shortFuncName returns the short name used for boundary pattern matching:
// "ReceiverType.MethodName" or "FuncName"
func shortFuncName(pkg string, fd *ast.FuncDecl) string {
	name := fd.Name.Name
	if fd.Recv != nil && len(fd.Recv.List) > 0 {
		recv := receiverTypeName(fd.Recv.List[0].Type)
		return recv + "." + name
	}
	return name
}

// receiverTypeName extracts the type name from a receiver expression,
// stripping any pointer dereference.
func receiverTypeName(expr ast.Expr) string {
	switch t := expr.(type) {
	case *ast.StarExpr:
		return receiverTypeName(t.X)
	case *ast.Ident:
		return t.Name
	case *ast.IndexExpr:
		// Generic receiver T[X]
		return receiverTypeName(t.X)
	}
	return "Unknown"
}
