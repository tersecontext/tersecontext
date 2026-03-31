package neo4j

import (
	"context"
	"fmt"

	"github.com/neo4j/neo4j-go-driver/v5/neo4j"
	querypb "github.com/tersecontext/tc/services/subgraph-expander/gen"
	"github.com/tersecontext/tc/services/subgraph-expander/internal/expander"
)

// FetchNeighbors fetches one hop of neighbors for the given stable IDs.
// For "impact" queries the direction is reversed (callers of seeds).
func (c *Client) FetchNeighbors(ctx context.Context, stableIDs []string, queryType string) ([]expander.BFSState, error) {
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	var cypher string
	switch queryType {
	case "impact":
		cypher = `
			MATCH (caller:Node)-[r:CALLS|TESTED_BY]->(seed:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND caller.active = true
			RETURN seed.stable_id   AS parent_id,
			       caller.stable_id AS stable_id,
			       caller.name      AS name,
			       caller.type      AS type,
			       caller.signature AS signature,
			       caller.docstring AS docstring,
			       caller.body      AS body,
			       type(r)          AS edge_type,
			       r.source         AS provenance,
			       r.frequency_ratio AS frequency_ratio`
	case "flow":
		cypher = `
			MATCH (seed:Node)-[r:CALLS|IMPORTS|INHERITS]->(neighbor:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND neighbor.active = true
			RETURN seed.stable_id     AS parent_id,
			       neighbor.stable_id AS stable_id,
			       neighbor.name      AS name,
			       neighbor.type      AS type,
			       neighbor.signature AS signature,
			       neighbor.docstring AS docstring,
			       neighbor.body      AS body,
			       type(r)            AS edge_type,
			       r.source           AS provenance,
			       r.frequency_ratio  AS frequency_ratio`
	case "lookup":
		cypher = `
			MATCH (seed:Node)-[r:DEFINES|IMPORTS|INHERITS]->(neighbor:Node)
			WHERE seed.stable_id IN $stable_ids
			  AND neighbor.active = true
			RETURN seed.stable_id     AS parent_id,
			       neighbor.stable_id AS stable_id,
			       neighbor.name      AS name,
			       neighbor.type      AS type,
			       neighbor.signature AS signature,
			       neighbor.docstring AS docstring,
			       neighbor.body      AS body,
			       type(r)            AS edge_type,
			       r.source           AS provenance,
			       r.frequency_ratio  AS frequency_ratio`
	default:
		return nil, fmt.Errorf("unknown query_type: %q", queryType)
	}

	result, err := session.Run(ctx, cypher, map[string]interface{}{"stable_ids": stableIDs})
	if err != nil {
		return nil, fmt.Errorf("neo4j FetchNeighbors: %w", err)
	}

	var nodes []expander.BFSState
	for result.Next(ctx) {
		rec := result.Record()
		n := expander.BFSState{}
		if v, ok := rec.Get("stable_id"); ok && v != nil {
			n.StableID = v.(string)
		}
		if v, ok := rec.Get("parent_id"); ok && v != nil {
			n.ParentID = v.(string)
		}
		if v, ok := rec.Get("name"); ok && v != nil {
			n.Name = v.(string)
		}
		if v, ok := rec.Get("type"); ok && v != nil {
			n.Type = v.(string)
		}
		if v, ok := rec.Get("signature"); ok && v != nil {
			n.Signature = v.(string)
		}
		if v, ok := rec.Get("docstring"); ok && v != nil {
			n.Docstring = v.(string)
		}
		if v, ok := rec.Get("body"); ok && v != nil {
			n.Body = v.(string)
		}
		if v, ok := rec.Get("edge_type"); ok && v != nil {
			n.EdgeType = v.(string)
		}
		if v, ok := rec.Get("provenance"); ok && v != nil {
			n.Provenance = v.(string)
		}
		if v, ok := rec.Get("frequency_ratio"); ok && v != nil {
			if f, ok := v.(float64); ok {
				n.FrequencyRatio = f
			}
		}
		nodes = append(nodes, n)
	}
	return nodes, result.Err()
}

// CollectEdges fetches all edges between surviving nodes.
func (c *Client) CollectEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, error) {
	if len(survivingIDs) == 0 {
		return nil, nil
	}
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	result, err := session.Run(ctx, `
		MATCH (a:Node)-[r]->(b:Node)
		WHERE a.stable_id IN $ids AND b.stable_id IN $ids
		RETURN a.stable_id AS source, b.stable_id AS target,
		       type(r) AS type, r.source AS provenance, r.frequency_ratio AS frequency_ratio`,
		map[string]interface{}{"ids": survivingIDs},
	)
	if err != nil {
		return nil, fmt.Errorf("neo4j CollectEdges: %w", err)
	}

	var edges []*querypb.SubgraphEdge
	for result.Next(ctx) {
		rec := result.Record()
		e := &querypb.SubgraphEdge{}
		if v, ok := rec.Get("source"); ok && v != nil {
			e.Source = v.(string)
		}
		if v, ok := rec.Get("target"); ok && v != nil {
			e.Target = v.(string)
		}
		if v, ok := rec.Get("type"); ok && v != nil {
			e.Type = v.(string)
		}
		if v, ok := rec.Get("provenance"); ok && v != nil {
			e.Provenance = v.(string)
		}
		if v, ok := rec.Get("frequency_ratio"); ok && v != nil {
			if f, ok := v.(float64); ok {
				e.FrequencyRatio = f
			}
		}
		edges = append(edges, e)
	}
	return edges, result.Err()
}

// CollectConflictEdges fetches conflict/dead edges involving any surviving node.
// These edges always appear in the output regardless of budget.
func (c *Client) CollectConflictEdges(ctx context.Context, survivingIDs []string) ([]*querypb.SubgraphEdge, []*querypb.SubgraphWarning, error) {
	if len(survivingIDs) == 0 {
		return nil, nil, nil
	}
	session := c.driver.NewSession(ctx, neo4j.SessionConfig{AccessMode: neo4j.AccessModeRead})
	defer session.Close(ctx)

	result, err := session.Run(ctx, `
		MATCH (a:Node)-[r]->(b:Node)
		WHERE a.stable_id IN $ids
		  AND r.source IN ['conflict', 'dead']
		RETURN a.stable_id AS source, b.stable_id AS target,
		       type(r) AS type, r.source AS provenance, r.detail AS detail`,
		map[string]interface{}{"ids": survivingIDs},
	)
	if err != nil {
		return nil, nil, fmt.Errorf("neo4j CollectConflictEdges: %w", err)
	}

	var edges []*querypb.SubgraphEdge
	var warnings []*querypb.SubgraphWarning
	for result.Next(ctx) {
		rec := result.Record()
		src, _ := rec.Get("source")
		tgt, _ := rec.Get("target")
		typ, _ := rec.Get("type")
		prov, _ := rec.Get("provenance")
		detail, _ := rec.Get("detail")

		srcStr, _ := src.(string)
		tgtStr, _ := tgt.(string)
		typStr, _ := typ.(string)
		provStr, _ := prov.(string)
		detailStr, _ := detail.(string)

		edges = append(edges, &querypb.SubgraphEdge{
			Source:     srcStr,
			Target:     tgtStr,
			Type:       typStr,
			Provenance: provStr,
		})
		warnType := "CONFLICT"
		if provStr == "dead" {
			warnType = "DEAD"
		}
		warnings = append(warnings, &querypb.SubgraphWarning{
			Type:   warnType,
			Source: srcStr,
			Target: tgtStr,
			Detail: detailStr,
		})
	}
	return edges, warnings, result.Err()
}
