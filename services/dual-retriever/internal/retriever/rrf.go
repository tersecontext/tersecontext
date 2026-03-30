package retriever

import (
	"sort"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
)

const rrfK = 60

// RankedNode represents a node returned from a single retrieval source.
type RankedNode struct {
	StableID string
	Name     string
	Type     string
	Source   string // "vector" or "graph"
}

// RRF performs Reciprocal Rank Fusion over multiple ranked lists and returns
// a merged, sorted slice of SeedNode protos capped at maxSeeds.
func RRF(lists [][]RankedNode, k int, maxSeeds int) []*querypb.SeedNode {
	type entry struct {
		score   float64
		name    string
		typ     string
		sources map[string]bool
	}

	entries := map[string]*entry{}

	for _, list := range lists {
		for rank, node := range list {
			e, ok := entries[node.StableID]
			if !ok {
				e = &entry{
					name:    node.Name,
					typ:     node.Type,
					sources: map[string]bool{},
				}
				entries[node.StableID] = e
			}
			e.score += 1.0 / float64(k+rank+1)
			e.sources[node.Source] = true
		}
	}

	result := make([]*querypb.SeedNode, 0, len(entries))
	for id, e := range entries {
		method := ""
		if e.sources["vector"] && e.sources["graph"] {
			method = "both"
		} else if e.sources["vector"] {
			method = "vector"
		} else {
			method = "graph"
		}
		result = append(result, &querypb.SeedNode{
			StableId:        id,
			Name:            e.name,
			Type:            e.typ,
			Score:           e.score,
			RetrievalMethod: method,
		})
	}

	sort.Slice(result, func(i, j int) bool {
		return result[i].Score > result[j].Score
	})

	if len(result) > maxSeeds {
		result = result[:maxSeeds]
	}
	return result
}
