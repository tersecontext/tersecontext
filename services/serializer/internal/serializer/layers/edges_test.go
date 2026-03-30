package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderEdges_groupsByType(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
		{Source: "sha256:fn5", Target: "sha256:fn6", Type: "CALLS", Provenance: "confirmed"},
		{Source: "sha256:fn3", Target: "sha256:file1", Type: "IMPORTS", Provenance: "confirmed"},
	}
	ids := map[string]string{
		"sha256:fn3":   "fn:1",
		"sha256:fn5":   "fn:2",
		"sha256:fn6":   "fn:3",
		"sha256:file1": "file:1",
	}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "CALLS:") {
		t.Errorf("missing CALLS: section: %s", out)
	}
	if !strings.Contains(out, "IMPORTS:") {
		t.Errorf("missing IMPORTS: section: %s", out)
	}
	if !strings.Contains(out, "fn:1 -> fn:2") {
		t.Errorf("missing fn:1 -> fn:2: %s", out)
	}
	if !strings.Contains(out, "fn:1 -> file:1") {
		t.Errorf("missing import edge: %s", out)
	}
}

func TestRenderEdges_runtimeOnlyTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "runtime-only"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "[runtime-only]") {
		t.Errorf("expected [runtime-only] tag: %s", out)
	}
}

func TestRenderEdges_conflictTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "CONFLICT", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "[CONFLICT]") {
		t.Errorf("expected [CONFLICT] tag: %s", out)
	}
}

func TestRenderEdges_deadTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, warnings, ids)
	if !strings.Contains(buf.String(), "[DEAD]") {
		t.Errorf("expected [DEAD] tag: %s", buf.String())
	}
}
