package expander

import (
	"context"
	"errors"
	"fmt"
	"log/slog"

	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
)

// ErrInvalidQueryType is returned for unrecognized query_type values.
// The server maps this to codes.InvalidArgument.
var ErrInvalidQueryType = errors.New("invalid query_type")

// SpecChecker fetches behavior spec text for a list of stable IDs.
// Returns a map of stable_id → spec text. IDs with no spec are absent.
type SpecChecker interface {
	FetchBehaviorSpecs(ctx context.Context, stableIDs []string) (map[string]string, error)
}

// EdgeCollector fetches edges between surviving nodes and conflict/dead edges.
type EdgeCollector interface {
	CollectEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, error)
	CollectConflictEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, []*querypb.SubgraphWarning, error)
}

// Expander orchestrates BFS traversal, scoring, pruning, and edge collection.
type Expander struct {
	fetcher   NeighborFetcher
	hydrator  SeedHydrator
	specs     SpecChecker
	collector EdgeCollector
}

// New creates an Expander with the given dependencies.
func New(fetcher NeighborFetcher, hydrator SeedHydrator, specs SpecChecker, collector EdgeCollector) *Expander {
	return &Expander{fetcher: fetcher, hydrator: hydrator, specs: specs, collector: collector}
}

// Expand performs BFS from seeds, prunes to budget, collects edges.
func (e *Expander) Expand(
	ctx context.Context,
	seeds []*querypb.SeedNode,
	queryType string,
	maxTokens int,
	hopDepth int,
	decay float64,
) (*querypb.RankedSubgraphResponse, error) {
	if len(seeds) == 0 {
		return &querypb.RankedSubgraphResponse{}, nil
	}

	// Validate query_type before touching the database.
	switch queryType {
	case "flow", "lookup", "impact":
		// valid
	default:
		return nil, fmt.Errorf("unknown query_type %q: %w", queryType, ErrInvalidQueryType)
	}

	// Convert proto seeds to BFSState at hop 0 with score 1.0.
	seedIDs := make([]string, len(seeds))
	for i, s := range seeds {
		seedIDs[i] = s.GetStableId()
	}

	// Hydrate seeds with full node data from Neo4j.
	hydrated, err := e.hydrator.HydrateSeeds(ctx, seedIDs)
	if err != nil {
		slog.Warn("seed hydration failed, using sparse seed data", "error", err)
		hydrated = map[string]BFSState{}
	}

	bfsSeeds := make([]BFSState, len(seeds))
	for i, s := range seeds {
		if h, ok := hydrated[s.GetStableId()]; ok {
			bfsSeeds[i] = h
			bfsSeeds[i].Score = 1.0
			bfsSeeds[i].Hop = 0
		} else {
			bfsSeeds[i] = BFSState{
				StableID: s.GetStableId(),
				Name:     s.GetName(),
				Type:     s.GetType(),
				Score:    1.0,
				Hop:      0,
			}
		}
	}

	// Fetch behavior specs for seeds (graceful degradation on error).
	specMap, err := e.specs.FetchBehaviorSpecs(ctx, seedIDs)
	if err != nil {
		slog.Warn("behavior spec lookup failed, treating seeds as having no spec", "error", err)
		specMap = map[string]string{}
	}

	// BFS traversal.
	visited, err := runBFS(ctx, bfsSeeds, e.fetcher, queryType, hopDepth, decay)
	if err != nil {
		return nil, fmt.Errorf("BFS traversal: %w", err)
	}

	allNodes := visitedToSlice(visited)
	totalCandidates := len(allNodes)

	// Prune to budget.
	surviving, budgetUsed := prune(allNodes, specMap, maxTokens)
	ids := survivingIDs(surviving)

	// Collect standard edges between surviving nodes.
	edges, err := e.collector.CollectEdges(ctx, ids)
	if err != nil {
		return nil, fmt.Errorf("collect edges: %w", err)
	}

	// Collect conflict/dead edges (always included, may overshoot budget).
	conflictEdges, warnings, err := e.collector.CollectConflictEdges(ctx, ids)
	if err != nil {
		return nil, fmt.Errorf("collect conflict edges: %w", err)
	}
	edges = append(edges, conflictEdges...)
	budgetUsed += len(conflictEdges) * tokensConflictEdge

	// Build response nodes.
	respNodes := make([]*querypb.SubgraphNode, 0, len(surviving))
	for _, n := range surviving {
		rn := &querypb.SubgraphNode{
			StableId:       n.StableID,
			Name:           n.Name,
			Type:           n.Type,
			Signature:      n.Signature,
			Docstring:      n.Docstring,
			Body:           n.Body,
			FilePath:       n.FilePath,
			Score:          n.Score,
			Hop:            int32(n.Hop),
			Provenance:     n.Provenance,
			ObservedCalls:  n.ObservedCalls,
			AvgLatencyMs:   n.AvgLatencyMs,
			BranchCoverage: n.BranchCoverage,
			RaisesObserved: n.RaisesObserved,
			SideEffects:    n.SideEffects,
		}
		if spec, ok := specMap[n.StableID]; ok {
			rn.BehaviorSpec = spec
		}
		respNodes = append(respNodes, rn)
	}

	return &querypb.RankedSubgraphResponse{
		Nodes:           respNodes,
		Edges:           edges,
		Warnings:        warnings,
		BudgetUsed:      int32(budgetUsed),
		TotalCandidates: int32(totalCandidates),
	}, nil
}
