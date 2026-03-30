package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderHeader writes Layer 1 (query header) to buf.
func RenderHeader(
	buf *strings.Builder,
	rawQuery, repo string,
	nodes []*querypb.SubgraphNode,
	warnings []*querypb.SubgraphWarning,
	ids map[string]string,
) {
	// Collect seed nodes (hop == 0), in ID order
	var seeds []*querypb.SubgraphNode
	for _, n := range nodes {
		if n.Hop == 0 {
			seeds = append(seeds, n)
		}
	}

	// Build SEEDS line
	var seedParts []string
	for _, s := range seeds {
		id := ids[s.StableId]
		seedParts = append(seedParts, id+" *")
	}
	seedsLine := strings.Join(seedParts, "  ")

	// Determine confidence from first seed
	conf, runs, coverage := confidence(seeds, warnings)

	fmt.Fprintf(buf, "QUERY:       %s\n", rawQuery)
	fmt.Fprintf(buf, "SEEDS:       %s\n", seedsLine)
	fmt.Fprintf(buf, "CONFIDENCE:  %s  (%d observed runs · %.0f%% coverage · %d warnings)\n",
		conf, runs, coverage*100, len(warnings))
	fmt.Fprintf(buf, "REPO:        %s\n", repo)
	buf.WriteString("\n")
}

func confidence(seeds []*querypb.SubgraphNode, warnings []*querypb.SubgraphWarning) (string, int32, float64) {
	if len(seeds) == 0 {
		return "low", 0, 0
	}
	seed := seeds[0]
	if len(warnings) > 0 || seed.ObservedCalls == 0 {
		return "low", seed.ObservedCalls, seed.BranchCoverage
	}
	if seed.ObservedCalls >= 10 && seed.BranchCoverage >= 0.8 {
		return "high", seed.ObservedCalls, seed.BranchCoverage
	}
	return "medium", seed.ObservedCalls, seed.BranchCoverage
}
