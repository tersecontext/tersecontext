package expander

import "sort"

const (
	tokensSeedWithSpec    = 60
	tokensSeedWithoutSpec = 120
	tokensPeripheral      = 20
	tokensConflictEdge    = 15
)

func estimateTokens(n BFSState, hasSpec bool) int {
	if n.Hop == 0 {
		if hasSpec {
			return tokensSeedWithSpec
		}
		return tokensSeedWithoutSpec
	}
	return tokensPeripheral
}

// prune sorts nodes by score descending and greedily includes them up to
// maxTokens. Seeds (hop=0) are always included regardless of budget.
func prune(nodes []BFSState, specMap map[string]string, maxTokens int) ([]BFSState, int) {
	sorted := make([]BFSState, len(nodes))
	copy(sorted, nodes)
	sort.Slice(sorted, func(i, j int) bool {
		return sorted[i].Score > sorted[j].Score
	})

	var surviving []BFSState
	used := 0
	for _, n := range sorted {
		_, hasSpec := specMap[n.StableID]
		cost := estimateTokens(n, hasSpec)
		if n.Hop == 0 || used+cost <= maxTokens {
			surviving = append(surviving, n)
			used += cost
		}
	}
	return surviving, used
}

// survivingIDs extracts stable_ids from a node slice.
func survivingIDs(nodes []BFSState) []string {
	ids := make([]string, len(nodes))
	for i, n := range nodes {
		ids[i] = n.StableID
	}
	return ids
}
