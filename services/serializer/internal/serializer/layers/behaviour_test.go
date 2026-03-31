package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderBehaviour_pathForSpec(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS"},
	}
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 23, BehaviorSpec: "1. hash_password   always   12ms"},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Body: "def _hash_password(pw, salt):\n  return bcrypt.hash(pw, salt)"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, edges, ids)
	out := buf.String()
	if !strings.Contains(out, "PATH fn:1") {
		t.Errorf("expected PATH for seed with spec: %s", out)
	}
	if !strings.Contains(out, "hash_password") {
		t.Errorf("expected spec content: %s", out)
	}
	if !strings.Contains(out, "BODY fn:2") {
		t.Errorf("expected BODY for hop-1 without spec: %s", out)
	}
	if !strings.Contains(out, "bcrypt") {
		t.Errorf("expected body content: %s", out)
	}
}

func TestRenderBehaviour_bodyWhenNoSpec(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Body: "def authenticate(user, pw):\n  return token"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "BODY fn:1") {
		t.Errorf("expected BODY: %s", out)
	}
	if !strings.Contains(out, "authenticate") {
		t.Errorf("expected body content: %s", out)
	}
}

func TestRenderBehaviour_onlySeedsAndHop1(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS"},
	}
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Body: "seed body"},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Body: "hop1 body"},
		{StableId: "sha256:fn6", Type: "function", Hop: 2, Body: "hop2 body — should not appear"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
		"sha256:fn6": "fn:3",
	}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, edges, ids)
	out := buf.String()
	if strings.Contains(out, "hop2 body") {
		t.Errorf("hop-2 node should not appear in behaviour layer: %s", out)
	}
	if !strings.Contains(out, "hop1 body") {
		t.Errorf("hop-1 node should appear: %s", out)
	}
}

func TestRenderBehaviour_emptyWhenNoBody(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, nil, ids)
	// node with no spec and no body should still get a BODY header, just empty
	out := buf.String()
	if !strings.Contains(out, "fn:1") {
		t.Errorf("seed node should still appear: %s", out)
	}
}
