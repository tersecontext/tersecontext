package expander

import (
	"context"
	"log/slog"
)

// BFSState holds all data for a discovered node during BFS traversal.
type BFSState struct {
	StableID       string
	Name           string
	Type           string
	Signature      string
	Docstring      string
	Body           string
	Score          float64
	Hop            int
	ParentID       string
	EdgeType       string
	Provenance     string
	ObservedCalls  int32
	AvgLatencyMs   float64
	BranchCoverage float64
	RaisesObserved []string
	SideEffects    []string
	FrequencyRatio float64
}

// SeedHydrator fetches full node data for seed nodes from the graph.
type SeedHydrator interface {
	HydrateSeeds(ctx context.Context, stableIDs []string) (map[string]BFSState, error)
}

// NeighborFetcher retrieves neighbors for a batch of nodes from the graph.
// The returned BFSState values have ParentID, EdgeType, and Provenance set;
// Score and Hop are set by the BFS loop.
type NeighborFetcher interface {
	FetchNeighbors(ctx context.Context, stableIDs []string, queryType string) ([]BFSState, error)
}

// runBFS performs application-side BFS from seed nodes up to hopDepth hops.
// Returns a visited map of stable_id → best-scoring BFSState.
func runBFS(ctx context.Context, seeds []BFSState, fetcher NeighborFetcher, queryType string, hopDepth int, decay float64) (map[string]BFSState, error) {
	visited := make(map[string]BFSState, len(seeds)*4)

	for _, s := range seeds {
		visited[s.StableID] = s
	}

	// hop < hopDepth (not <=): seeds are pre-seeded at hop 0 before the loop.
	// Iteration hop=0 fetches neighbors of hop-0 nodes → placed at hop 1.
	// Iteration hop=hopDepth-1 fetches neighbors of hop-(hopDepth-1) nodes → placed at hop hopDepth.
	// Result: nodes up to hopDepth are discovered. The spec pseudocode uses <= but
	// that would produce an extra level; < is correct here.
	for hop := 0; hop < hopDepth; hop++ {
		// Collect all nodes currently at this hop level.
		var currentIDs []string
		for _, n := range visited {
			if n.Hop == hop {
				currentIDs = append(currentIDs, n.StableID)
			}
		}
		if len(currentIDs) == 0 {
			break
		}

		neighbors, err := fetcher.FetchNeighbors(ctx, currentIDs, queryType)
		if err != nil {
			return nil, err
		}

		for _, n := range neighbors {
			parent := visited[n.ParentID]
			score := computeScore(parent.Score, decay, n.EdgeType, n.Provenance)
			if score <= 0 {
				slog.Warn("dropping node with zero score — unknown edge type?",
					"stable_id", n.StableID,
					"edge_type", n.EdgeType,
				)
				continue
			}
			n.Score = score
			n.Hop = hop + 1

			if existing, seen := visited[n.StableID]; seen {
				if n.Score <= existing.Score {
					continue
				}
			}
			visited[n.StableID] = n
		}
	}

	return visited, nil
}

// visitedToSlice converts the visited map to a slice.
func visitedToSlice(visited map[string]BFSState) []BFSState {
	nodes := make([]BFSState, 0, len(visited))
	for _, n := range visited {
		nodes = append(nodes, n)
	}
	return nodes
}
