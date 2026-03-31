package executor

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

type Executor struct {
	sessionDir string
	lockPath   string
}

func New(sessionDir string) *Executor {
	return &Executor{
		sessionDir: sessionDir,
		lockPath:   filepath.Join(sessionDir, ".lock"),
	}
}

func (e *Executor) CreateLock() error {
	return os.WriteFile(e.lockPath, []byte(fmt.Sprintf("%d", os.Getpid())), 0644)
}

func (e *Executor) ReleaseLock() {
	os.Remove(e.lockPath)
}

func (e *Executor) Timeout(binaryType string, requestedSeconds int) time.Duration {
	if binaryType == "server" {
		return time.Duration(requestedSeconds*2) * time.Second
	}
	return time.Duration(requestedSeconds) * time.Second
}

func (e *Executor) RunTestBinary(ctx context.Context, binaryPath, testPattern string, timeout time.Duration, socketPath string) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	args := []string{"-test.timeout=" + timeout.String()}
	if testPattern != "" {
		args = append(args, "-test.run="+testPattern)
	}

	cmd := exec.CommandContext(ctx, binaryPath, args...)
	cmd.Env = append(os.Environ(), "TRACERT_SOCKET="+socketPath)
	cmd.Dir = e.sessionDir

	output, err := cmd.CombinedOutput()
	if ctx.Err() == context.DeadlineExceeded {
		return fmt.Errorf("test execution timed out after %v", timeout)
	}
	if err != nil {
		return fmt.Errorf("test binary failed: %w\n%s", err, output)
	}
	return nil
}
