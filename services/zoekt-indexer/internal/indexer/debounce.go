package indexer

import (
	"sync"
	"time"
)

// Debouncer fires a callback once per repo per debounce window.
// Multiple triggers within the window collapse into a single call.
type Debouncer struct {
	window time.Duration
	mu     sync.Mutex
	timers map[string]*time.Timer
}

// NewDebouncer returns a Debouncer with the given window.
func NewDebouncer(window time.Duration) *Debouncer {
	return &Debouncer{window: window, timers: map[string]*time.Timer{}}
}

// Trigger schedules fn(repo) to fire after the debounce window.
// If already scheduled, resets the timer.
func (d *Debouncer) Trigger(repo string, fn func(string)) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if t, ok := d.timers[repo]; ok {
		t.Stop()
	}
	d.timers[repo] = time.AfterFunc(d.window, func() {
		fn(repo)
		d.mu.Lock()
		delete(d.timers, repo)
		d.mu.Unlock()
	})
}
