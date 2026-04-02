package retriever

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
)

// GraphSearcher searches a code index and returns ranked nodes.
type GraphSearcher interface {
	Search(ctx context.Context, keywords []string, symbols []string, repo string, limit int) ([]RankedNode, error)
}

// fileLineMatch represents a file+line result from zoekt.
type fileLineMatch struct {
	File  string
	Line  int
	Score float64
}

// nodeResolver maps file+line matches to graph nodes.
type nodeResolver interface {
	Resolve(ctx context.Context, matches []fileLineMatch, repo string) ([]RankedNode, error)
}

// ZoektSearcher implements GraphSearcher using the zoekt HTTP search API.
type ZoektSearcher struct {
	zoektURL   string
	httpClient *http.Client
	resolver   nodeResolver
}

// NewZoektSearcher creates a ZoektSearcher backed by the given zoekt URL and Neo4j driver.
func NewZoektSearcher(zoektURL string, driver neo4j.DriverWithContext, httpClient *http.Client) *ZoektSearcher {
	if httpClient == nil {
		httpClient = &http.Client{}
	}
	return &ZoektSearcher{
		zoektURL:   zoektURL,
		httpClient: httpClient,
		resolver:   &neo4jNodeResolver{driver: driver},
	}
}

// Search queries zoekt with the given keywords and symbols, then maps results to Neo4j nodes.
// Symbols use exact-match prefix ("sym:"); keywords use substring search.
// Returns empty slice (no error) when both keywords and symbols are empty.
func (z *ZoektSearcher) Search(ctx context.Context, keywords, symbols []string, repo string, limit int) ([]RankedNode, error) {
	if len(keywords) == 0 && len(symbols) == 0 {
		return nil, nil
	}

	q := buildZoektQuery(keywords, symbols)

	body, _ := json.Marshal(map[string]interface{}{
		"Q": q,
		"Opts": map[string]interface{}{
			"Repo":          repo,
			"MaxMatchCount": limit * 2,
		},
	})

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, z.zoektURL+"/search", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("zoekt search request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := z.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("zoekt search: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("zoekt search status %d: %s", resp.StatusCode, b)
	}

	var result struct {
		Result struct {
			Files []struct {
				FileName    string `json:"FileName"`
				Repository  string `json:"Repository"`
				LineMatches []struct {
					LineNumber int     `json:"LineNumber"`
					Score      float64 `json:"Score"`
				} `json:"LineMatches"`
			} `json:"Files"`
		} `json:"Result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("zoekt decode: %w", err)
	}

	var matches []fileLineMatch
	for _, f := range result.Result.Files {
		for _, lm := range f.LineMatches {
			matches = append(matches, fileLineMatch{
				File:  f.FileName,
				Line:  lm.LineNumber,
				Score: lm.Score,
			})
		}
	}
	if len(matches) == 0 {
		return nil, nil
	}

	return z.resolver.Resolve(ctx, matches, repo)
}

// buildZoektQuery builds a zoekt query string.
// Symbols are wrapped in "sym:" for identifier-aware matching.
// Keywords are used as plain substrings.
func buildZoektQuery(keywords, symbols []string) string {
	var parts []string
	for _, s := range symbols {
		parts = append(parts, "sym:"+s)
	}
	parts = append(parts, keywords...)
	return strings.Join(parts, " OR ")
}

// neo4jNodeResolver maps file+line matches to Neo4j stable_ids via line-range lookup.
type neo4jNodeResolver struct {
	driver neo4j.DriverWithContext
}

const resolveNodesQuery = `
UNWIND $matches AS m
MATCH (n:Node {repo: $repo, file_path: m.file, active: true})
WHERE n.start_line <= m.line AND n.end_line >= m.line
WITH n, m, (n.end_line - n.start_line) AS span
ORDER BY span ASC
WITH m.line AS line, m.file AS file, m.score AS zoekt_score,
     head(collect(n)) AS n
RETURN n.stable_id AS stable_id, n.name AS name, n.type AS type, zoekt_score
`

func (r *neo4jNodeResolver) Resolve(ctx context.Context, matches []fileLineMatch, repo string) ([]RankedNode, error) {
	session := r.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	matchParams := make([]map[string]interface{}, len(matches))
	for i, m := range matches {
		matchParams[i] = map[string]interface{}{
			"file":  m.File,
			"line":  m.Line,
			"score": m.Score,
		}
	}

	result, err := session.Run(ctx, resolveNodesQuery, map[string]interface{}{
		"matches": matchParams,
		"repo":    repo,
	})
	if err != nil {
		return nil, fmt.Errorf("neo4j resolve: %w", err)
	}

	// Deduplicate by stable_id, keeping max zoekt_score.
	seen := map[string]float64{}
	var nodes []RankedNode
	for result.Next(ctx) {
		rec := result.Record()
		sid, _ := rec.Get("stable_id")
		name, _ := rec.Get("name")
		typ, _ := rec.Get("type")
		score, _ := rec.Get("zoekt_score")
		s := sid.(string)
		sc, _ := score.(float64)
		if prev, exists := seen[s]; !exists || sc > prev {
			seen[s] = sc
			if !exists {
				nodes = append(nodes, RankedNode{StableID: s, Name: name.(string), Type: typ.(string), Source: "graph"})
			}
		}
	}
	if err := result.Err(); err != nil {
		return nil, fmt.Errorf("neo4j resolve result: %w", err)
	}
	return nodes, nil
}
