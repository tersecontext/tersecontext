package rewriter

import "testing"

func TestBoundaryPatternMatching(t *testing.T) {
	patterns := []string{"*.Handler*", "db.*", "http.Client.*"}
	m := NewPatternMatcher(patterns)

	tests := []struct {
		funcName string
		want     bool
	}{
		{"OrderHandler.Create", false},  // *.Handler* requires single segment before dot
		{"db.Query", true},
		{"db.Exec", true},
		{"http.Client.Do", true},   // filepath.Match: * matches any chars except /, so http.Client.* matches http.Client.Do
		{"processOrder", false},
		{"utils.Hash", false},
	}

	for _, tt := range tests {
		got := m.IsBoundary(tt.funcName)
		if got != tt.want {
			t.Errorf("IsBoundary(%q) = %v, want %v", tt.funcName, got, tt.want)
		}
	}
}
