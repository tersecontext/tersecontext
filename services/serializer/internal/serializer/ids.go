package serializer

import (
	"fmt"
	"sort"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// AssignIDs returns a map from node StableId to short display ID.
// IDs are deterministic: within each type group, nodes are sorted
// by hop (ascending) then score (descending).
func AssignIDs(nodes []*querypb.SubgraphNode) map[string]string {
	byType := map[string][]*querypb.SubgraphNode{}
	for _, n := range nodes {
		byType[n.Type] = append(byType[n.Type], n)
	}

	prefixes := map[string]string{
		"function": "fn",
		"method":   "fn",
		"class":    "cls",
		"file":     "file",
	}
	// counters are per-prefix (not per-type), so function and method share "fn" counter
	counters := map[string]int{}
	ids := map[string]string{}

	// Process types in stable order so the full ID map is deterministic
	typeOrder := []string{"function", "method", "class", "file"}
	for _, nodeType := range typeOrder {
		group, ok := byType[nodeType]
		if !ok {
			continue
		}
		sort.Slice(group, func(i, j int) bool {
			if group[i].Hop != group[j].Hop {
				return group[i].Hop < group[j].Hop
			}
			return group[i].Score > group[j].Score
		})
		prefix := prefixes[nodeType]
		for _, n := range group {
			counters[prefix]++
			ids[n.StableId] = fmt.Sprintf("%s:%d", prefix, counters[prefix])
		}
	}
	return ids
}
