package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderWarnings_conflict(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "CONFLICT", Source: "sha256:fn3", Target: "sha256:fn5",
			Detail: "static: always called · observed: 0/10 runs"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "CONFLICT") {
		t.Errorf("missing CONFLICT: %s", out)
	}
	if !strings.Contains(out, "fn:1") {
		t.Errorf("missing source id: %s", out)
	}
	if !strings.Contains(out, "fn:2") {
		t.Errorf("missing target id: %s", out)
	}
	if !strings.Contains(out, "static: always called") {
		t.Errorf("missing detail: %s", out)
	}
}

func TestRenderWarnings_dead(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5", Detail: "in AST · never observed"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "DEAD") {
		t.Errorf("missing DEAD: %s", out)
	}
}

func TestRenderWarnings_stale(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "STALE", Source: "sha256:fn3", Detail: "spec last run 45 days ago"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "STALE") {
		t.Errorf("missing STALE: %s", out)
	}
	if !strings.Contains(out, "fn:1") {
		t.Errorf("missing id: %s", out)
	}
}

func TestRenderWarnings_empty(t *testing.T) {
	var buf strings.Builder
	layers.RenderWarnings(&buf, nil, nil)
	if buf.Len() != 0 {
		t.Errorf("expected empty output for no warnings, got: %q", buf.String())
	}
}
