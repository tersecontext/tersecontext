package passes

import (
	"go/ast"

	"golang.org/x/tools/go/ast/astutil"
)

// WrapGoroutines replaces every GoStmt in the file with a tracert.Go(...) call.
// `go f(args)` becomes `tracert.Go(func() { f(args) })`.
func WrapGoroutines(file *ast.File) {
	astutil.Apply(file, func(c *astutil.Cursor) bool {
		gs, ok := c.Node().(*ast.GoStmt)
		if !ok {
			return true
		}
		wrapped := wrapGoStmt(gs)
		c.Replace(&ast.ExprStmt{X: wrapped})
		return true
	}, nil)
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
