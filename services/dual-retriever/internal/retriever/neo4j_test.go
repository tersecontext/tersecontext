package retriever

import (
	"testing"
)

func TestBuildFullTextQuery(t *testing.T) {
	tests := []struct {
		name     string
		keywords []string
		symbols  []string
		want     string
	}{
		{
			name:     "symbols and keywords",
			keywords: []string{"auth", "login"},
			symbols:  []string{"authenticate", "AuthService"},
			want:     "authenticate OR AuthService OR auth OR login",
		},
		{
			name:     "keywords only",
			keywords: []string{"auth", "login"},
			symbols:  nil,
			want:     "auth OR login",
		},
		{
			name:     "symbols only",
			symbols:  []string{"AuthService"},
			keywords: nil,
			want:     "AuthService",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := buildFullTextQuery(tt.keywords, tt.symbols)
			if got != tt.want {
				t.Errorf("buildFullTextQuery() = %q, want %q", got, tt.want)
			}
		})
	}
}
