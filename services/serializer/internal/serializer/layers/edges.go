package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderEdges writes Layer 4 (edge topology) to buf, grouped by edge type.
func RenderEdges(
	buf *strings.Builder,
	edges []*querypb.SubgraphEdge,
	warnings []*querypb.SubgraphWarning,
	ids map[string]string,
) {
	// Build lookup: (source, target) -> warning type
	warnKey := func(src, tgt string) string { return src + "→" + tgt }
	warnMap := map[string]string{}
	for _, w := range warnings {
		if w.Type == "CONFLICT" || w.Type == "DEAD" {
			warnMap[warnKey(w.Source, w.Target)] = w.Type
		}
	}

	// Group edges by type, preserving order of first appearance
	seen := map[string]bool{}
	var typeOrder []string
	byType := map[string][]*querypb.SubgraphEdge{}
	for _, e := range edges {
		if !seen[e.Type] {
			seen[e.Type] = true
			typeOrder = append(typeOrder, e.Type)
		}
		byType[e.Type] = append(byType[e.Type], e)
	}

	for _, edgeType := range typeOrder {
		fmt.Fprintf(buf, "%s:\n", edgeType)
		for _, e := range byType[edgeType] {
			srcID := ids[e.Source]
			tgtID := ids[e.Target]
			tag := ""
			if e.Provenance == "runtime-only" {
				tag = "  [runtime-only]"
			} else if wt, ok := warnMap[warnKey(e.Source, e.Target)]; ok {
				tag = fmt.Sprintf("  [%s]        see warnings", wt)
			}
			fmt.Fprintf(buf, "  %s -> %s%s\n", srcID, tgtID, tag)
		}
	}
	buf.WriteString("\n")
}
