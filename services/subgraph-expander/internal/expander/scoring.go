package expander

var edgeTypeWeights = map[string]float64{
	"CALLS":       1.0,
	"IMPORTS":     0.8,
	"INHERITS":    0.7,
	"MODIFIED_BY": 0.6,
	"TESTED_BY":   0.5,
}

var provenanceWeights = map[string]float64{
	"confirmed": 1.0,
	"dynamic":   0.9,
	"static":    0.8,
	"conflict":  0.6,
	"":          0.8,
}

func edgeTypeWeight(edgeType string) float64 {
	return edgeTypeWeights[edgeType]
}

func provenanceWeight(prov string) float64 {
	if w, ok := provenanceWeights[prov]; ok {
		return w
	}
	return provenanceWeights[""]
}

func computeScore(parentScore, decay float64, edgeType, provenance string) float64 {
	return parentScore * decay * edgeTypeWeight(edgeType) * provenanceWeight(provenance)
}
