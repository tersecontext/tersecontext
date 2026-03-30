package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderBehaviour writes Layer 5 (behaviour) to buf.
// Includes seed nodes (hop=0) and hop-1 nodes reachable via CALLS from a seed.
func RenderBehaviour(
	buf *strings.Builder,
	nodes []*querypb.SubgraphNode,
	edges []*querypb.SubgraphEdge,
	ids map[string]string,
) {
	// Build set of seed stable IDs
	seedIDs := map[string]bool{}
	for _, n := range nodes {
		if n.Hop == 0 {
			seedIDs[n.StableId] = true
		}
	}

	// Build set of hop-1 nodes directly called by a seed
	hop1Targets := map[string]bool{}
	for _, e := range edges {
		if e.Type == "CALLS" && seedIDs[e.Source] {
			hop1Targets[e.Target] = true
		}
	}

	// Render seeds first, then hop-1 callees
	rendered := map[string]bool{}
	render := func(n *querypb.SubgraphNode) {
		if rendered[n.StableId] {
			return
		}
		rendered[n.StableId] = true
		id := ids[n.StableId]
		if n.BehaviorSpec != "" {
			seedMark := ""
			if n.Hop == 0 {
				seedMark = fmt.Sprintf(" (* seed · %d runs observed)", n.ObservedCalls)
			}
			fmt.Fprintf(buf, "PATH %s%s\n", id, seedMark)
			for _, line := range strings.Split(n.BehaviorSpec, "\n") {
				fmt.Fprintf(buf, "  %s\n", line)
			}
		} else {
			seedMark := ""
			if n.Hop == 0 {
				seedMark = " (* static · no spec)"
			}
			fmt.Fprintf(buf, "BODY %s%s\n", id, seedMark)
			for _, line := range strings.Split(n.Body, "\n") {
				fmt.Fprintf(buf, "  %s\n", line)
			}
		}
		buf.WriteString("\n")
	}

	for _, n := range nodes {
		if n.Hop == 0 {
			render(n)
		}
	}
	for _, n := range nodes {
		if hop1Targets[n.StableId] {
			render(n)
		}
	}
}
