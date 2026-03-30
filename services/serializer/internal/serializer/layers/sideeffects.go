package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderSideEffects writes Layer 6 (side effects) to buf.
// Writes nothing if no nodes have side effects.
func RenderSideEffects(buf *strings.Builder, nodes []*querypb.SubgraphNode, ids map[string]string) {
	for _, n := range nodes {
		if len(n.SideEffects) == 0 {
			continue
		}
		id := ids[n.StableId]
		fmt.Fprintf(buf, "SIDE_EFFECTS %s:\n", id)
		for _, effect := range n.SideEffects {
			fmt.Fprintf(buf, "  %s\n", effect)
		}
		buf.WriteString("\n")
	}
}
