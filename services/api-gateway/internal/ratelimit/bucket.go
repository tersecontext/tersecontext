package ratelimit

import (
	"sync"
	"time"
)

const (
	maxTokens  = 10
	refillRate = 10.0 / 60.0 // tokens per second
)

type clockFn func() time.Time

type bucket struct {
	mu         sync.Mutex
	tokens     float64
	lastRefill time.Time
}

// Limiter is a per-key token bucket rate limiter.
type Limiter struct {
	buckets sync.Map
	clock   clockFn
}

// NewLimiter creates a Limiter using the real clock.
func NewLimiter() *Limiter {
	return &Limiter{clock: time.Now}
}

// NewLimiterWithClock creates a Limiter with an injectable clock for testing.
func NewLimiterWithClock(clock clockFn) *Limiter {
	return &Limiter{clock: clock}
}

// SetClock replaces the clock function (test helper).
func (l *Limiter) SetClock(clock clockFn) {
	l.clock = clock
}

// Allow returns true if the key is within the rate limit, false otherwise.
func (l *Limiter) Allow(key string) bool {
	now := l.clock()
	v, _ := l.buckets.LoadOrStore(key, &bucket{tokens: maxTokens, lastRefill: now})
	b := v.(*bucket)

	b.mu.Lock()
	defer b.mu.Unlock()

	elapsed := now.Sub(b.lastRefill).Seconds()
	b.tokens += elapsed * refillRate
	if b.tokens > maxTokens {
		b.tokens = maxTokens
	}
	b.lastRefill = now

	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}
