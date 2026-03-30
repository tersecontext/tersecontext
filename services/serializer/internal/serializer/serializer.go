package serializer

import (
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

// Serializer renders a RankedSubgraph into a six-layer context document.
type Serializer struct{}

func New() *Serializer { return &Serializer{} }

// Serialize returns the rendered document, an estimated token count, and any error.
func (s *Serializer) Serialize(req *querypb.SerializeRequest) (string, int32, error) {
	sg := req.GetSubgraph()
	nodes := sg.GetNodes()
	edges := sg.GetEdges()
	warnings := sg.GetWarnings()
	rawQuery := req.GetIntent().GetRawQuery()
	repo := req.GetRepo()

	ids := AssignIDs(nodes)

	var buf strings.Builder

	// Layer 1 — always present
	layers.RenderHeader(&buf, rawQuery, repo, nodes, warnings, ids)

	// Layer 2 — omit if no warnings
	if len(warnings) > 0 {
		layers.RenderWarnings(&buf, warnings, ids)
	}

	// Layer 3 — always present
	layers.RenderRegistry(&buf, nodes, ids)

	// Layer 4 — always present
	layers.RenderEdges(&buf, edges, warnings, ids)

	// Layer 5 — always present (seeds + hop-1 callees)
	layers.RenderBehaviour(&buf, nodes, edges, ids)

	// Layer 6 — omit if no side effects
	hasSideEffects := false
	for _, n := range nodes {
		if len(n.SideEffects) > 0 {
			hasSideEffects = true
			break
		}
	}
	if hasSideEffects {
		layers.RenderSideEffects(&buf, nodes, ids)
	}

	doc := buf.String()
	return doc, estimateTokens(doc), nil
}

// estimateTokens returns a rough token count (chars / 4).
func estimateTokens(doc string) int32 {
	return int32(len(doc) / 4)
}
