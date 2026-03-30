package serializer_test

import (
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
)

func TestAssignIDs_deterministic(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids1 := serializer.AssignIDs(nodes)
	ids2 := serializer.AssignIDs(nodes)
	if ids1["sha256:fn3"] != ids2["sha256:fn3"] {
		t.Errorf("non-deterministic: got %q then %q", ids1["sha256:fn3"], ids2["sha256:fn3"])
	}
}

func TestAssignIDs_seedFirst(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids := serializer.AssignIDs(nodes)
	if ids["sha256:fn3"] != "fn:1" {
		t.Errorf("seed node: want fn:1, got %q", ids["sha256:fn3"])
	}
	if ids["sha256:fn5"] != "fn:2" {
		t.Errorf("hop-1 node: want fn:2, got %q", ids["sha256:fn5"])
	}
}

func TestAssignIDs_multipleTypes(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3",  Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:cls1", Type: "class",    Hop: 1, Score: 0.8},
		{StableId: "sha256:fn5",  Type: "function", Hop: 1, Score: 0.7},
	}
	ids := serializer.AssignIDs(nodes)
	if ids["sha256:fn3"] != "fn:1"  { t.Errorf("want fn:1,  got %q", ids["sha256:fn3"]) }
	if ids["sha256:fn5"] != "fn:2"  { t.Errorf("want fn:2,  got %q", ids["sha256:fn5"]) }
	if ids["sha256:cls1"] != "cls:1" { t.Errorf("want cls:1, got %q", ids["sha256:cls1"]) }
}
