package tracert

import "sync"

// ringBuffer is a fixed-size circular buffer for trace events.
// When full, oldest events are dropped.
type ringBuffer struct {
	mu    sync.Mutex
	buf   []Event
	size  int
	head  int // next write position
	count int
}

func newRingBuffer(size int) *ringBuffer {
	return &ringBuffer{
		buf:  make([]Event, size),
		size: size,
	}
}

func (rb *ringBuffer) Write(e Event) {
	rb.mu.Lock()
	rb.buf[rb.head] = e
	rb.head = (rb.head + 1) % rb.size
	if rb.count < rb.size {
		rb.count++
	}
	rb.mu.Unlock()
}

// Drain returns all buffered events in order and resets the buffer.
func (rb *ringBuffer) Drain() []Event {
	rb.mu.Lock()
	defer rb.mu.Unlock()

	if rb.count == 0 {
		return nil
	}

	events := make([]Event, rb.count)
	start := (rb.head - rb.count + rb.size) % rb.size
	for i := 0; i < rb.count; i++ {
		events[i] = rb.buf[(start+i)%rb.size]
	}
	rb.count = 0
	rb.head = 0
	return events
}
