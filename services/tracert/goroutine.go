package tracert

import (
	"sync"
	"sync/atomic"

	"github.com/petermattis/goid"
)

// goroutineCtx holds per-goroutine tracing state.
type goroutineCtx struct {
	id       uint64 // trace-local goroutine ID
	parentID uint64 // parent goroutine's trace-local ID
}

var (
	goroutineMap    sync.Map // goid → *goroutineCtx
	nextGoroutineID atomic.Uint64
)

func init() {
	nextGoroutineID.Store(1)
}

// currentGoroutineCtx returns or creates the context for the current goroutine.
func currentGoroutineCtx() *goroutineCtx {
	gid := goid.Get()
	if ctx, ok := goroutineMap.Load(gid); ok {
		return ctx.(*goroutineCtx)
	}
	ctx := &goroutineCtx{
		id: nextGoroutineID.Add(1) - 1,
	}
	goroutineMap.Store(gid, ctx)
	return ctx
}

// registerChildGoroutine sets up tracing context for a new goroutine,
// inheriting the parent's trace-local ID.
func registerChildGoroutine(parentTraceID uint64) {
	gid := goid.Get()
	ctx := &goroutineCtx{
		id:       nextGoroutineID.Add(1) - 1,
		parentID: parentTraceID,
	}
	goroutineMap.Store(gid, ctx)
}

// resetGoroutineState clears all goroutine tracking (for testing).
func resetGoroutineState() {
	goroutineMap.Range(func(key, _ any) bool {
		goroutineMap.Delete(key)
		return true
	})
	nextGoroutineID.Store(1)
}
