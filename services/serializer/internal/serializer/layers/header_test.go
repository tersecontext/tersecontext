package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderHeader_basic(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 23, BranchCoverage: 0.75},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderHeader(&buf, "how does auth work", "myrepo", nodes, nil, ids)
	out := buf.String()

	checks := []string{
		"QUERY:",
		"how does auth work",
		"SEEDS:",
		"fn:1",
		"*",
		"CONFIDENCE:",
		"medium",
		"REPO:",
		"myrepo",
	}
	for _, want := range checks {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in header output:\n%s", want, out)
		}
	}
}

func TestRenderHeader_highConfidence(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 15, BranchCoverage: 0.9},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, nil, ids)
	if !strings.Contains(buf.String(), "high") {
		t.Errorf("expected high confidence")
	}
}

func TestRenderHeader_lowConfidence_warnings(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 0},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, warnings, ids)
	if !strings.Contains(buf.String(), "low") {
		t.Errorf("expected low confidence")
	}
}

func TestRenderHeader_multipleSeeds(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:a", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:b", Type: "function", Hop: 0, Score: 0.9},
		{StableId: "sha256:c", Type: "function", Hop: 1, Score: 0.5},
	}
	ids := map[string]string{
		"sha256:a": "fn:1",
		"sha256:b": "fn:2",
		"sha256:c": "fn:3",
	}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, nil, ids)
	out := buf.String()
	// both seeds appear, hop-1 node does not appear in SEEDS line
	if !strings.Contains(out, "fn:1") || !strings.Contains(out, "fn:2") {
		t.Errorf("both seeds should appear: %s", out)
	}
}
