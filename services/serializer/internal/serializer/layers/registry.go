package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderRegistry writes Layer 3 (node registry) to buf.
func RenderRegistry(buf *strings.Builder, nodes []*querypb.SubgraphNode, ids map[string]string) {
	for _, n := range nodes {
		id := ids[n.StableId]
		idCol := id
		if n.Hop == 0 {
			idCol = id + " *"
		}
		sig := n.Signature
		if sig == "" {
			sig = n.Name
		}
		pill := provenancePill(n)
		fmt.Fprintf(buf, "%-12s  %-50s  %s\n", idCol, sig, pill)
	}
	buf.WriteString("\n")
}

func provenancePill(n *querypb.SubgraphNode) string {
	if n.Provenance == "runtime-only" {
		return "runtime-only"
	}
	if n.ObservedCalls > 0 || n.BranchCoverage > 0 {
		pct := int(n.BranchCoverage * 100)
		return fmt.Sprintf("spec.%d%%", pct)
	}
	return "static"
}
