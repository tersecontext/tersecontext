package collector

import "testing"

func TestParseWireEvent(t *testing.T) {
	line := `{"t":"call","f":"auth.Login","file":"auth.go","l":10,"ts":0.5,"g":1,"s":1}`
	event, err := ParseWireEvent([]byte(line))
	if err != nil {
		t.Fatal(err)
	}
	if event.Type != "call" {
		t.Errorf("expected call, got %s", event.Type)
	}
	if event.Func != "auth.Login" {
		t.Errorf("expected auth.Login, got %s", event.Func)
	}
}

func TestMapWireTypeToTraceEvent(t *testing.T) {
	tests := []struct {
		wireType string
		want     string
	}{
		{"call", "call"},
		{"ret", "return"},
		{"panic", "exception"},
	}
	for _, tt := range tests {
		got := MapEventType(tt.wireType)
		if got != tt.want {
			t.Errorf("MapEventType(%q) = %q, want %q", tt.wireType, got, tt.want)
		}
	}
}

func TestGroupEventsByGoroutine(t *testing.T) {
	events := []WireEvent{
		{Type: "call", Func: "main.A", GoroutineID: 1, SpanID: 1},
		{Type: "call", Func: "main.B", GoroutineID: 2, SpanID: 2},
		{Type: "ret", GoroutineID: 1, SpanID: 1},
		{Type: "ret", GoroutineID: 2, SpanID: 2},
	}

	groups := GroupByGoroutine(events)
	if len(groups) != 2 {
		t.Fatalf("expected 2 goroutine groups, got %d", len(groups))
	}
	if len(groups[1]) != 2 || len(groups[2]) != 2 {
		t.Error("each goroutine should have 2 events")
	}
}
