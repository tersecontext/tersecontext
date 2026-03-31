package rewriter

import "path/filepath"

// PatternMatcher matches function names against glob patterns for boundary detection.
type PatternMatcher struct {
	patterns []string
}

func NewPatternMatcher(patterns []string) *PatternMatcher {
	return &PatternMatcher{patterns: patterns}
}

// IsBoundary returns true if the function name matches any boundary pattern.
func (m *PatternMatcher) IsBoundary(funcName string) bool {
	for _, p := range m.patterns {
		matched, _ := filepath.Match(p, funcName)
		if matched {
			return true
		}
	}
	return false
}
