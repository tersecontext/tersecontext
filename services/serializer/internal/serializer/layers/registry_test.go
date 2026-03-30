package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderRegistry_onePerNode(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "authenticate(user: User, pw: str) -> Token",
			ObservedCalls: 23, BranchCoverage: 0.75},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Signature: "_hash_password(pw: str, salt: bytes) -> str",
			Provenance: "confirmed"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	lines := strings.Split(strings.TrimSpace(out), "\n")
	// header line + 2 node lines + blank line separator
	if len(lines) < 2 {
		t.Fatalf("expected at least 2 lines, got %d:\n%s", len(lines), out)
	}
	if !strings.Contains(out, "fn:1") { t.Errorf("missing fn:1: %s", out) }
	if !strings.Contains(out, "fn:2") { t.Errorf("missing fn:2: %s", out) }
	if !strings.Contains(out, "authenticate") { t.Errorf("missing signature: %s", out) }
}

func TestRenderRegistry_seedMarked(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "foo()", Name: "foo", ObservedCalls: 5},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	// seed node: id should appear with *
	if !strings.Contains(out, "fn:1 *") {
		t.Errorf("seed node should have * marker: %s", out)
	}
}

func TestRenderRegistry_provenancePills(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "a()", ObservedCalls: 10, BranchCoverage: 0.8},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Signature: "b()", Provenance: "confirmed"},
		{StableId: "sha256:fn6", Type: "function", Hop: 1, Score: 0.5,
			Signature: "c()", Provenance: "runtime-only"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
		"sha256:fn6": "fn:3",
	}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "spec.80%") { t.Errorf("missing spec pill: %s", out) }
	if !strings.Contains(out, "static")    { t.Errorf("missing static pill: %s", out) }
	if !strings.Contains(out, "runtime-only") { t.Errorf("missing runtime-only pill: %s", out) }
}
