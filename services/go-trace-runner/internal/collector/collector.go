package collector

import (
	"bufio"
	"encoding/json"
	"net"
)

type WireEvent struct {
	Type        string  `json:"t"`
	Func        string  `json:"f"`
	File        string  `json:"file,omitempty"`
	Line        int     `json:"l,omitempty"`
	TimestampMs float64 `json:"ts"`
	GoroutineID uint64  `json:"g"`
	SpanID      uint64  `json:"s"`
	Args        any     `json:"args,omitempty"`
	ReturnVal   any     `json:"ret,omitempty"`
	ExcType     string  `json:"exc,omitempty"`
}

func ParseWireEvent(line []byte) (WireEvent, error) {
	var e WireEvent
	err := json.Unmarshal(line, &e)
	return e, err
}

// MapEventType converts wire types to RawTrace TraceEvent types.
func MapEventType(wireType string) string {
	switch wireType {
	case "ret":
		return "return"
	case "panic":
		return "exception"
	default:
		return wireType
	}
}

func GroupByGoroutine(events []WireEvent) map[uint64][]WireEvent {
	groups := make(map[uint64][]WireEvent)
	for _, e := range events {
		groups[e.GoroutineID] = append(groups[e.GoroutineID], e)
	}
	return groups
}

// ReadFromSocket listens on a Unix socket and reads all events until connection closes.
func ReadFromSocket(socketPath string) ([]WireEvent, error) {
	ln, err := net.Listen("unix", socketPath)
	if err != nil {
		return nil, err
	}
	defer ln.Close()

	conn, err := ln.Accept()
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	var events []WireEvent
	scanner := bufio.NewScanner(conn)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		e, err := ParseWireEvent(scanner.Bytes())
		if err != nil {
			continue
		}
		events = append(events, e)
	}
	return events, scanner.Err()
}
