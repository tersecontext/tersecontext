package expander

import (
	"math"
	"testing"
)

func TestEdgeTypeWeight_KnownTypes(t *testing.T) {
	cases := []struct {
		edgeType string
		want     float64
	}{
		{"CALLS", 1.0},
		{"IMPORTS", 0.8},
		{"INHERITS", 0.7},
		{"MODIFIED_BY", 0.6},
		{"TESTED_BY", 0.5},
	}
	for _, tc := range cases {
		got := edgeTypeWeight(tc.edgeType)
		if math.Abs(got-tc.want) > 1e-9 {
			t.Errorf("edgeTypeWeight(%q) = %v, want %v", tc.edgeType, got, tc.want)
		}
	}
}

func TestEdgeTypeWeight_Unknown(t *testing.T) {
	// unknown edge types get weight 0 (not traversed)
	if edgeTypeWeight("UNKNOWN") != 0 {
		t.Error("expected 0 for unknown edge type")
	}
}

func TestProvenanceWeight_KnownTypes(t *testing.T) {
	cases := []struct {
		prov string
		want float64
	}{
		{"confirmed", 1.0},
		{"dynamic", 0.9},
		{"static", 0.8},
		{"conflict", 0.6},
		{"", 0.8}, // missing = static
	}
	for _, tc := range cases {
		got := provenanceWeight(tc.prov)
		if math.Abs(got-tc.want) > 1e-9 {
			t.Errorf("provenanceWeight(%q) = %v, want %v", tc.prov, got, tc.want)
		}
	}
}

func TestComputeScore(t *testing.T) {
	// hop-1 CALLS confirmed: 1.0 * 0.7 * 1.0 * 1.0 = 0.7
	got := computeScore(1.0, 0.7, "CALLS", "confirmed")
	if math.Abs(got-0.7) > 1e-9 {
		t.Errorf("computeScore = %v, want 0.7", got)
	}
}

func TestComputeScore_ConfirmedBeatsStatic(t *testing.T) {
	confirmed := computeScore(1.0, 0.7, "CALLS", "confirmed")
	static := computeScore(1.0, 0.7, "CALLS", "static")
	if confirmed <= static {
		t.Errorf("confirmed (%v) should beat static (%v)", confirmed, static)
	}
}
