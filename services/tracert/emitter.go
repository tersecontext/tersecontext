package tracert

import (
	"bufio"
	"encoding/json"
	"log"
	"net"
	"sync"
)

var (
	emitterConn   net.Conn
	emitterWriter *bufio.Writer
	emitterMu     sync.Mutex
	emitterBuf    *ringBuffer
)

// initEmitter connects to the Unix socket for event emission.
func initEmitter(socketPath string) {
	emitterBuf = newRingBuffer(65536) // 64K event ring buffer

	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		log.Printf("tracert: failed to connect to %s: %v (events will be buffered)", socketPath, err)
		return
	}
	emitterConn = conn
	emitterWriter = bufio.NewWriterSize(conn, 64*1024)
}

// emitEvent buffers an event.
func emitEvent(e Event) {
	emitterBuf.Write(e)
}

// flushEmitter writes all buffered events to the socket.
func flushEmitter() {
	events := emitterBuf.Drain()
	if len(events) == 0 || emitterConn == nil {
		return
	}

	emitterMu.Lock()
	defer emitterMu.Unlock()

	enc := json.NewEncoder(emitterWriter)
	for _, e := range events {
		if err := enc.Encode(e); err != nil {
			log.Printf("tracert: encode error: %v", err)
		}
	}
	if err := emitterWriter.Flush(); err != nil {
		log.Printf("tracert: flush error: %v", err)
	}
}
