package retriever

import (
	"context"
	"log/slog"
	"time"

	querypb "github.com/tersecontext/tc/services/dual-retriever/gen"
)

const (
	DefaultMaxSeeds  = 8
	retrievalTimeout = 500 * time.Millisecond
)

// Retriever orchestrates parallel fan-out to vector and graph search,
// merging results with Reciprocal Rank Fusion.
type Retriever struct {
	embedder Embedder
	vector   VectorSearcher
	graph    GraphSearcher
}

// NewRetriever creates a Retriever with the given dependencies.
func NewRetriever(embedder Embedder, vector VectorSearcher, graph GraphSearcher) *Retriever {
	return &Retriever{embedder: embedder, vector: vector, graph: graph}
}

type retrievalResult struct {
	nodes  []RankedNode
	source string
	err    error
}

// Retrieve fans out to vector and graph search in parallel, applies a
// 500 ms timeout for graceful degradation, and merges results via RRF.
func (r *Retriever) Retrieve(ctx context.Context, embedQuery string, keywords, symbols []string, repo string, maxSeeds int) ([]*querypb.SeedNode, error) {
	if maxSeeds <= 0 {
		maxSeeds = DefaultMaxSeeds
	}

	start := time.Now()
	limit := maxSeeds * 2

	vectorCh := make(chan retrievalResult, 1)
	graphCh := make(chan retrievalResult, 1)

	runVector := embedQuery != ""
	runGraph := len(keywords) > 0 || len(symbols) > 0

	if runVector {
		go func() {
			vStart := time.Now()
			vec, err := r.embedder.Embed(ctx, embedQuery)
			if err != nil {
				slog.Warn("embed failed", "error", err, "vector_ms", time.Since(vStart).Milliseconds())
				vectorCh <- retrievalResult{source: "vector", err: err}
				return
			}
			nodes, err := r.vector.Search(ctx, vec, repo, limit)
			slog.Info("vector search complete", "vector_ms", time.Since(vStart).Milliseconds(), "vector_count", len(nodes))
			vectorCh <- retrievalResult{nodes: nodes, source: "vector", err: err}
		}()
	}

	if runGraph {
		go func() {
			gStart := time.Now()
			query := buildFullTextQuery(keywords, symbols)
			nodes, err := r.graph.Search(ctx, query, symbols, repo, limit)
			slog.Info("graph search complete", "graph_ms", time.Since(gStart).Milliseconds(), "graph_count", len(nodes))
			graphCh <- retrievalResult{nodes: nodes, source: "graph", err: err}
		}()
	}

	timer := time.NewTimer(retrievalTimeout)
	defer timer.Stop()

	expected := 0
	if runVector {
		expected++
	}
	if runGraph {
		expected++
	}

	var vectorNodes, graphNodes []RankedNode
	timedOut := false
	collected := 0

	for collected < expected && !timedOut {
		select {
		case res := <-vectorCh:
			collected++
			if res.err == nil {
				vectorNodes = res.nodes
			}
		case res := <-graphCh:
			collected++
			if res.err == nil {
				graphNodes = res.nodes
			}
		case <-timer.C:
			timedOut = true
			slog.Warn("retrieval timeout — using partial results")
		}
	}

	// Non-blocking drain after timeout
	if timedOut {
		select {
		case res := <-vectorCh:
			if res.err == nil {
				vectorNodes = res.nodes
			}
		default:
		}
		select {
		case res := <-graphCh:
			if res.err == nil {
				graphNodes = res.nodes
			}
		default:
		}
	}

	var lists [][]RankedNode
	if len(vectorNodes) > 0 {
		lists = append(lists, vectorNodes)
	}
	if len(graphNodes) > 0 {
		lists = append(lists, graphNodes)
	}

	result := RRF(lists, rrfK, maxSeeds)

	slog.Info("retrieve complete",
		"retrieve_ms", time.Since(start).Milliseconds(),
		"timeout", timedOut,
		"vector_count", len(vectorNodes),
		"graph_count", len(graphNodes),
		"result_count", len(result),
	)

	return result, nil
}
