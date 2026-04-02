package indexer

import (
	"fmt"
	"os/exec"
	"path/filepath"
)

// Indexer invokes zoekt-index to build or update index shards.
type Indexer struct {
	indexDir string
	reposDir string
}

// NewIndexer creates an Indexer that writes shards to indexDir from reposDir.
func NewIndexer(indexDir, reposDir string) *Indexer {
	return &Indexer{indexDir: indexDir, reposDir: reposDir}
}

// IndexRepo runs zoekt-index for the given repo name.
// repoPath is constructed as filepath.Join(reposDir, repo) — no shell expansion.
func (idx *Indexer) IndexRepo(repo string) error {
	repoPath := filepath.Join(idx.reposDir, repo)
	cmd := exec.Command("zoekt-index", "-index", idx.indexDir, repoPath)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("zoekt-index %s: %w\noutput: %s", repo, err, out)
	}
	return nil
}
