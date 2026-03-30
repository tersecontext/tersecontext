package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderSideEffects_present(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0,
			SideEffects: []string{
				"DB READ users WHERE id=$1",
				"CACHE SET session:{id} TTL 3600",
			},
		},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "SIDE_EFFECTS fn:1") {
		t.Errorf("missing header: %s", out)
	}
	if !strings.Contains(out, "DB READ") {
		t.Errorf("missing db read: %s", out)
	}
	if !strings.Contains(out, "CACHE SET") {
		t.Errorf("missing cache set: %s", out)
	}
}

func TestRenderSideEffects_empty(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, SideEffects: nil},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	if buf.Len() != 0 {
		t.Errorf("expected empty output when no side effects, got: %q", buf.String())
	}
}

func TestRenderSideEffects_multipleNodes(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", SideEffects: []string{"DB READ users"}},
		{StableId: "sha256:fn5", SideEffects: []string{"HTTP OUT POST /api/notify"}},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "fn:1") {
		t.Errorf("missing fn:1: %s", out)
	}
	if !strings.Contains(out, "fn:2") {
		t.Errorf("missing fn:2: %s", out)
	}
}
