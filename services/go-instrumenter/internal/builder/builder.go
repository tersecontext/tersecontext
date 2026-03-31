package builder

import (
	"fmt"
	"os/exec"
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
	cmd := exec.Command("go", "test", "-c", "-o", outputPath, pkg)
	cmd.Dir = repoDir
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("go test -c: %w\n%s", err, out)
	}
	return nil
}
