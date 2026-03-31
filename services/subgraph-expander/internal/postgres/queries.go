package postgres

import "context"

// FetchBehaviorSpecs returns a map of stable_id → spec text for the given
// stable IDs. IDs with no behavior spec are absent from the map.
func (c *Client) FetchBehaviorSpecs(ctx context.Context, stableIDs []string) (map[string]string, error) {
	if len(stableIDs) == 0 {
		return map[string]string{}, nil
	}

	rows, err := c.pool.Query(ctx,
		`SELECT stable_id, spec FROM behavior_specs WHERE stable_id = ANY($1)`,
		stableIDs,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	result := make(map[string]string)
	for rows.Next() {
		var stableID, spec string
		if err := rows.Scan(&stableID, &spec); err != nil {
			return nil, err
		}
		result[stableID] = spec
	}
	return result, rows.Err()
}
