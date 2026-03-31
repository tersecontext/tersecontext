package tracert

import (
	"bufio"
	"encoding/json"
	"net"
	"path/filepath"
	"testing"
	"time"
)

func TestEmitterWritesJSONLinesToSocket(t *testing.T) {
	dir := t.TempDir()
	socketPath := filepath.Join(dir, "trace.sock")

	// Start a listener
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()

	received := make(chan Event, 10)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		scanner := bufio.NewScanner(conn)
		for scanner.Scan() {
			var e Event
			if err := json.Unmarshal(scanner.Bytes(), &e); err == nil {
				received <- e
			}
		}
	}()

	// Give listener time to start
	time.Sleep(10 * time.Millisecond)

	initEmitter(socketPath)
	emitEvent(Event{Type: "call", Func: "test.Fn", GoroutineID: 1, SpanID: 1})
	flushEmitter()

	select {
	case e := <-received:
		if e.Type != "call" || e.Func != "test.Fn" {
			t.Errorf("unexpected event: %+v", e)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timeout waiting for event")
	}
}

func TestEmitterGracefulWhenNoSocket(t *testing.T) {
	// Should not panic when socket doesn't exist
	initEmitter("/nonexistent/path/trace.sock")
	emitEvent(Event{Type: "call", Func: "test.Fn"})
	flushEmitter()
	// No panic = pass
}
