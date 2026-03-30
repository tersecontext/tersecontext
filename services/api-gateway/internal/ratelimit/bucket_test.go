package ratelimit_test

import (
	"testing"
	"time"

	"github.com/tersecontext/tc/services/api-gateway/internal/ratelimit"
)

func TestAllow_UnderLimit(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		if !l.Allow("repo:ip") {
			t.Fatalf("request %d should be allowed", i+1)
		}
	}
}

func TestAllow_ExceedsLimit(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		l.Allow("repo:ip")
	}
	if l.Allow("repo:ip") {
		t.Fatal("11th request should be denied")
	}
}

func TestAllow_DifferentKeysIndependent(t *testing.T) {
	l := ratelimit.NewLimiter()
	for i := 0; i < 10; i++ {
		l.Allow("repo-a:ip")
	}
	// repo-b bucket is full — should still allow
	if !l.Allow("repo-b:ip") {
		t.Fatal("different key should have its own bucket")
	}
}

func TestAllow_RefillOverTime(t *testing.T) {
	l := ratelimit.NewLimiterWithClock(func() time.Time {
		return time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	})
	for i := 0; i < 10; i++ {
		l.Allow("repo:ip")
	}
	// Advance clock by 6 seconds → 1 token refilled (10/60 * 6 = 1.0)
	l.SetClock(func() time.Time {
		return time.Date(2026, 1, 1, 0, 0, 6, 0, time.UTC)
	})
	if !l.Allow("repo:ip") {
		t.Fatal("should allow after refill time")
	}
	if l.Allow("repo:ip") {
		t.Fatal("only 1 token should have refilled")
	}
}
