package coverage

import (
	"bufio"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
)

// ParseCoverFunc parses `go tool cover -func` output.
// Returns map of "file:funcName" → covered (true if coverage > 0%).
func ParseCoverFunc(output string) map[string]bool {
	result := make(map[string]bool)
	scanner := bufio.NewScanner(strings.NewReader(output))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "total:") {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 3 {
			continue
		}
		fileLine := parts[0]
		funcName := parts[1]
		covStr := parts[len(parts)-1]

		// fileLine is "path/to/file.go:linenum:" — take everything before the first colon.
		firstColon := strings.Index(fileLine, ":")
		if firstColon < 0 {
			continue
		}
		file := fileLine[:firstColon]

		covered := covStr != "0.0%"
		key := file + ":" + funcName
		result[key] = covered
	}
	return result
}

// RunCoverProfile runs `go test -coverprofile` and returns covered functions.
func RunCoverProfile(repoPath string, testPattern string) (map[string]bool, error) {
	coverFile := filepath.Join(repoPath, "cover.out")

	args := []string{"test", "-coverprofile=" + coverFile, "-covermode=atomic"}
	if testPattern != "" {
		args = append(args, "-run="+testPattern)
	}
	args = append(args, "./...")

	cmd := exec.Command("go", args...)
	cmd.Dir = repoPath
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("go test -coverprofile failed: %w\n%s", err, out)
	}

	funcCmd := exec.Command("go", "tool", "cover", "-func="+coverFile)
	funcCmd.Dir = repoPath
	out, err := funcCmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("go tool cover -func failed: %w\n%s", err, out)
	}

	return ParseCoverFunc(string(out)), nil
}
