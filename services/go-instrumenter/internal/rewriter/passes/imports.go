package passes

import (
	"go/ast"
	"go/token"

	"golang.org/x/tools/go/ast/astutil"
)

// EnsureImport adds importPath to file's import block if not already present.
// Uses astutil.AddImport which handles deduplication.
func EnsureImport(fset *token.FileSet, file *ast.File, importPath string) {
	astutil.AddImport(fset, file, importPath)
}
