package serializer_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
)

func mockRequest() *querypb.SerializeRequest {
	return &querypb.SerializeRequest{
		Subgraph: &querypb.RankedSubgraphResponse{
			Nodes: []*querypb.SubgraphNode{
				{
					StableId:       "sha256:fn3",
					Name:           "authenticate",
					Type:           "function",
					Signature:      "authenticate(user: User, pw: str) -> Token",
					Body:           "def authenticate(self, user, pw):\n    ...",
					Score:          1.0,
					Hop:            0,
					Provenance:     "confirmed",
					ObservedCalls:  23,
					AvgLatencyMs:   28,
					BranchCoverage: 0.75,
				},
				{
					StableId:   "sha256:fn5",
					Name:       "_hash_password",
					Type:       "function",
					Signature:  "_hash_password(pw: str, salt: bytes) -> str",
					Score:      0.7,
					Hop:        1,
					Provenance: "confirmed",
				},
			},
			Edges: []*querypb.SubgraphEdge{
				{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
			},
		},
		Intent: &querypb.QueryIntentResponse{RawQuery: "how does auth work"},
		Repo:   "test",
	}
}

func TestSerializer_allLayers(t *testing.T) {
	s := serializer.New()
	doc, tokens, err := s.Serialize(mockRequest())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if tokens <= 0 {
		t.Errorf("expected positive token count, got %d", tokens)
	}

	checks := []string{
		"QUERY:",
		"how does auth work",
		"SEEDS:",
		"fn:1",
		"CONFIDENCE:",
		"REPO:",
		"test",
		"fn:1",
		"fn:2",
		"authenticate",
		"CALLS:",
		"fn:1 -> fn:2",
		"BODY fn:1",
	}
	for _, want := range checks {
		if !strings.Contains(doc, want) {
			t.Errorf("missing %q in document:\n%s", want, doc)
		}
	}
}

func TestSerializer_noWarningsLayer(t *testing.T) {
	s := serializer.New()
	req := mockRequest()
	req.Subgraph.Warnings = nil
	doc, _, _ := s.Serialize(req)
	if strings.Contains(doc, "CONFLICT") || strings.Contains(doc, "DEAD") || strings.Contains(doc, "STALE") {
		t.Errorf("Layer 2 should be absent when no warnings: %s", doc)
	}
}

func TestSerializer_warningsLayerPresent(t *testing.T) {
	s := serializer.New()
	req := mockRequest()
	req.Subgraph.Warnings = []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5", Detail: "never observed"},
	}
	doc, _, _ := s.Serialize(req)
	if !strings.Contains(doc, "DEAD") {
		t.Errorf("Layer 2 should be present when warnings exist: %s", doc)
	}
}
