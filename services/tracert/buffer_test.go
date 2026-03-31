package tracert

import (
	"fmt"
	"sync"
	"testing"
)

func TestRingBufferWriteAndDrain(t *testing.T) {
	rb := newRingBuffer(8)

	rb.Write(Event{Type: "call", Func: "a"})
	rb.Write(Event{Type: "ret", Func: "a"})

	events := rb.Drain()
	if len(events) != 2 {
		t.Fatalf("expected 2, got %d", len(events))
	}
	if events[0].Func != "a" || events[1].Type != "ret" {
		t.Error("unexpected event content")
	}

	// After drain, buffer should be empty
	events = rb.Drain()
	if len(events) != 0 {
		t.Fatalf("expected 0 after drain, got %d", len(events))
	}
}

func TestRingBufferOverflow(t *testing.T) {
	rb := newRingBuffer(4)

	for i := 0; i < 6; i++ {
		rb.Write(Event{Type: "call", Func: "x"})
	}

	events := rb.Drain()
	// Should have the last 4 events (oldest dropped)
	if len(events) != 4 {
		t.Fatalf("expected 4, got %d", len(events))
	}
}

func TestRingBufferConcurrentWrites(t *testing.T) {
	rb := newRingBuffer(1024)
	const writers = 10
	const eventsPerWriter = 100

	var wg sync.WaitGroup
	wg.Add(writers)
	for w := 0; w < writers; w++ {
		go func(id int) {
			defer wg.Done()
			for i := 0; i < eventsPerWriter; i++ {
				rb.Write(Event{Type: "call", Func: fmt.Sprintf("g%d.fn%d", id, i)})
			}
		}(w)
	}
	wg.Wait()

	events := rb.Drain()
	if len(events) != writers*eventsPerWriter {
		t.Fatalf("expected %d events, got %d", writers*eventsPerWriter, len(events))
	}
}
