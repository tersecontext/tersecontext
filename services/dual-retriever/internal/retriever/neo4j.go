package retriever

import (
	"context"
	"fmt"
	"strings"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// GraphSearcher searches a graph store and returns ranked nodes.
type GraphSearcher interface {
	Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error)
}

// Neo4jSearcher searches Neo4j using full-text and direct name matching.
type Neo4jSearcher struct {
	driver neo4j.DriverWithContext
}

// NewNeo4jSearcher creates a Neo4jSearcher using the given Neo4j driver.
func NewNeo4jSearcher(driver neo4j.DriverWithContext) *Neo4jSearcher {
	return &Neo4jSearcher{driver: driver}
}

// buildFullTextQuery combines symbols and keywords into a Neo4j full-text
// query string joined with OR.
func buildFullTextQuery(keywords, symbols []string) string {
	terms := make([]string, 0, len(symbols)+len(keywords))
	terms = append(terms, symbols...)
	terms = append(terms, keywords...)
	return strings.Join(terms, " OR ")
}

// Search executes a direct name match for exact symbols and a full-text
// search, returning deduplicated ranked nodes.
func (n *Neo4jSearcher) Search(ctx context.Context, query string, symbols []string, repo string, limit int) ([]RankedNode, error) {
	session := n.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	seen := map[string]bool{}
	var nodes []RankedNode

	// Direct name match for exact symbols (prepended — highest priority)
	if len(symbols) > 0 {
		directResult, err := session.Run(ctx,
			`MATCH (n:Node {repo: $repo, active: true})
			 WHERE n.name IN $symbols OR n.qualified_name IN $symbols
			 RETURN n.stable_id AS stable_id, n.name AS name, n.type AS type`,
			map[string]interface{}{"repo": repo, "symbols": symbols},
		)
		if err != nil {
			return nil, fmt.Errorf("neo4j direct match: %w", err)
		}
		for directResult.Next(ctx) {
			record := directResult.Record()
			stableID, _ := record.Get("stable_id")
			name, _ := record.Get("name")
			typ, _ := record.Get("type")
			sid := stableID.(string)
			if !seen[sid] {
				seen[sid] = true
				nodes = append(nodes, RankedNode{
					StableID: sid,
					Name:     name.(string),
					Type:     typ.(string),
					Source:   "graph",
				})
			}
		}
	}

	// Full-text search
	if query != "" {
		ftResult, err := session.Run(ctx,
			`CALL db.index.fulltext.queryNodes("node_search", $query)
			 YIELD node, score
			 WHERE node.repo = $repo AND node.active = true
			 RETURN node.stable_id AS stable_id, node.name AS name, node.type AS type, score
			 ORDER BY score DESC
			 LIMIT $limit`,
			map[string]interface{}{"query": query, "repo": repo, "limit": limit},
		)
		if err != nil {
			return nil, fmt.Errorf("neo4j fulltext search: %w", err)
		}
		for ftResult.Next(ctx) {
			record := ftResult.Record()
			stableID, _ := record.Get("stable_id")
			name, _ := record.Get("name")
			typ, _ := record.Get("type")
			sid := stableID.(string)
			if !seen[sid] {
				seen[sid] = true
				nodes = append(nodes, RankedNode{
					StableID: sid,
					Name:     name.(string),
					Type:     typ.(string),
					Source:   "graph",
				})
			}
		}
	}

	return nodes, nil
}
