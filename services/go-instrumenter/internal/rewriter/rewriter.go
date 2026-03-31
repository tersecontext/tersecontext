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

	"golang.org/x/tools/go/ast/astutil"
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

		enterCall := buildEnterCall(funcID, fd, isBoundary)
		deferStmt := buildDeferStmt()

		// Prepend: __span := tracert.Enter(...) and defer func() { ... }()
		newStmts := []ast.Stmt{
			&ast.AssignStmt{
				Lhs: []ast.Expr{ast.NewIdent("__span")},
				Tok: token.DEFINE,
				Rhs: []ast.Expr{enterCall},
			},
			deferStmt,
		}
		fd.Body.List = append(newStmts, fd.Body.List...)
		return true
	})

	// Second pass: replace GoStmt nodes with tracert.Go(...) calls
	astutil.Apply(file, func(c *astutil.Cursor) bool {
		gs, ok := c.Node().(*ast.GoStmt)
		if !ok {
			return true
		}

		// Replace `go f(args)` or `go func() { ... }()` with
		// `tracert.Go(func() { f(args) })` or `tracert.Go(func() { (func(){...})() })`
		wrapped := wrapGoStmt(gs)
		c.Replace(&ast.ExprStmt{X: wrapped})
		return true
	}, nil)

	// Add the tracert import
	astutil.AddImport(fset, file, tracertImport)

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

// buildEnterCall constructs the `tracert.Enter(funcID, args...)` call expression.
// For boundary functions, all parameter names are appended as arguments.
func buildEnterCall(funcID string, fd *ast.FuncDecl, isBoundary bool) *ast.CallExpr {
	args := []ast.Expr{
		&ast.BasicLit{Kind: token.STRING, Value: fmt.Sprintf("%q", funcID)},
	}

	if isBoundary && fd.Type.Params != nil {
		for _, field := range fd.Type.Params.List {
			for _, name := range field.Names {
				args = append(args, ast.NewIdent(name.Name))
			}
		}
	}

	return &ast.CallExpr{
		Fun: &ast.SelectorExpr{
			X:   ast.NewIdent("tracert"),
			Sel: ast.NewIdent("Enter"),
		},
		Args: args,
	}
}

// buildDeferStmt constructs:
//
//	defer func() {
//	    if __r := recover(); __r != nil {
//	        tracert.ExitPanic(__span, __r)
//	        panic(__r)
//	    }
//	    tracert.Exit(__span)
//	}()
func buildDeferStmt() *ast.DeferStmt {
	// tracert.ExitPanic(__span, __r)
	exitPanicCall := &ast.ExprStmt{X: &ast.CallExpr{
		Fun: &ast.SelectorExpr{
			X:   ast.NewIdent("tracert"),
			Sel: ast.NewIdent("ExitPanic"),
		},
		Args: []ast.Expr{
			ast.NewIdent("__span"),
			ast.NewIdent("__r"),
		},
	}}

	// panic(__r)
	rePanic := &ast.ExprStmt{X: &ast.CallExpr{
		Fun:  ast.NewIdent("panic"),
		Args: []ast.Expr{ast.NewIdent("__r")},
	}}

	// if __r := recover(); __r != nil { ... }
	ifStmt := &ast.IfStmt{
		Init: &ast.AssignStmt{
			Lhs: []ast.Expr{ast.NewIdent("__r")},
			Tok: token.DEFINE,
			Rhs: []ast.Expr{&ast.CallExpr{Fun: ast.NewIdent("recover")}},
		},
		Cond: &ast.BinaryExpr{
			X:  ast.NewIdent("__r"),
			Op: token.NEQ,
			Y:  ast.NewIdent("nil"),
		},
		Body: &ast.BlockStmt{
			List: []ast.Stmt{exitPanicCall, rePanic},
		},
	}

	// tracert.Exit(__span)
	exitCall := &ast.ExprStmt{X: &ast.CallExpr{
		Fun: &ast.SelectorExpr{
			X:   ast.NewIdent("tracert"),
			Sel: ast.NewIdent("Exit"),
		},
		Args: []ast.Expr{ast.NewIdent("__span")},
	}}

	// func() { ... }()
	funcLit := &ast.FuncLit{
		Type: &ast.FuncType{Params: &ast.FieldList{}},
		Body: &ast.BlockStmt{
			List: []ast.Stmt{ifStmt, exitCall},
		},
	}

	return &ast.DeferStmt{
		Call: &ast.CallExpr{
			Fun: funcLit,
		},
	}
}

// wrapGoStmt wraps a GoStmt's call expression inside tracert.Go(func() { ... }).
// For `go f(args)` → `tracert.Go(func() { f(args) })`
// For `go func() { ... }()` → `tracert.Go(func() { (func(){ ... })() })`
func wrapGoStmt(gs *ast.GoStmt) *ast.CallExpr {
	innerStmt := &ast.ExprStmt{X: gs.Call}

	wrappedFunc := &ast.FuncLit{
		Type: &ast.FuncType{Params: &ast.FieldList{}},
		Body: &ast.BlockStmt{
			List: []ast.Stmt{innerStmt},
		},
	}

	return &ast.CallExpr{
		Fun: &ast.SelectorExpr{
			X:   ast.NewIdent("tracert"),
			Sel: ast.NewIdent("Go"),
		},
		Args: []ast.Expr{wrappedFunc},
	}
}
