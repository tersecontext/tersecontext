package passes

import (
	"fmt"
	"go/ast"
	"go/token"
)

// InjectEntry prepends __span := tracert.Enter(...) and a defer func() { ... }()
// into the body of fn. isBoundary controls whether parameter names are passed
// to Enter.
func InjectEntry(fn *ast.FuncDecl, funcID string, isBoundary bool) {
	enterCall := buildEnterCall(funcID, fn, isBoundary)
	deferStmt := buildDeferStmt()

	newStmts := []ast.Stmt{
		&ast.AssignStmt{
			Lhs: []ast.Expr{ast.NewIdent("__span")},
			Tok: token.DEFINE,
			Rhs: []ast.Expr{enterCall},
		},
		deferStmt,
	}
	fn.Body.List = append(newStmts, fn.Body.List...)
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
