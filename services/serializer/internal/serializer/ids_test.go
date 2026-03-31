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
	// Reversed order — same nodes, different input sequence
	reversed := []*querypb.SubgraphNode{nodes[1], nodes[0]}

	ids1 := serializer.AssignIDs(nodes)
	ids2 := serializer.AssignIDs(reversed)

	for k, v := range ids1 {
		if ids2[k] != v {
			t.Errorf("non-deterministic for key %q: order1=%q order2=%q", k, v, ids2[k])
		}
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
