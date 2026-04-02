package indexer

import (
	"sync"
	"testing"
	"time"
)

func TestDebouncer_FiresOncePerWindow(t *testing.T) {
	var mu sync.Mutex
	calls := 0
	d := NewDebouncer(20 * time.Millisecond)

	fn := func(repo string) {
		mu.Lock()
		calls++
		mu.Unlock()
	}

	d.Trigger("my-repo", fn)
	d.Trigger("my-repo", fn)
	d.Trigger("my-repo", fn)

	time.Sleep(50 * time.Millisecond)

	mu.Lock()
	got := calls
	mu.Unlock()

	if got != 1 {
		t.Errorf("expected 1 call after debounce, got %d", got)
	}
}

func TestDebouncer_DifferentReposFireIndependently(t *testing.T) {
	var mu sync.Mutex
	fired := map[string]int{}
	d := NewDebouncer(20 * time.Millisecond)

	fn := func(repo string) {
		mu.Lock()
		fired[repo]++
		mu.Unlock()
	}

	d.Trigger("repo-a", fn)
	d.Trigger("repo-b", fn)

	time.Sleep(50 * time.Millisecond)

	mu.Lock()
	defer mu.Unlock()
	if fired["repo-a"] != 1 || fired["repo-b"] != 1 {
		t.Errorf("expected 1 call each, got %v", fired)
	}
}
