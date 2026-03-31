# Serializer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the serializer gRPC service that receives a RankedSubgraph and renders the six-layer plain-text context document returned to the LLM.

**Architecture:** A gRPC server implements `QueryService/Serialize`. The `Serializer` orchestrator first assigns deterministic short IDs to all nodes, then delegates each of the six output layers to a dedicated renderer in the `layers/` sub-package. All data comes from the gRPC request (proto fields on SubgraphNode/SubgraphEdge/SubgraphWarning) — no Postgres in this phase.

**Tech Stack:** Go 1.23, gRPC/protobuf, log/slog

---

## Scope note

Postgres integration (BehaviorSpec fetch, STALE detection) is deferred. Layer 5 uses the `behavior_spec` string already carried on `SubgraphNode` in the proto; if it is empty the node's `body` field is used instead. The `REPO` line in Layer 1 omits `@sha:{commit_sha}` until Postgres is added.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `proto/query.proto` | Modify | Add `repo` (field 3) to `SerializeRequest` |
| `Makefile` | Modify | Add serializer gen target |
| `services/serializer/gen/` | Generate | Proto stubs for the serializer module |
| `services/serializer/go.mod` | Modify | Add grpc + protobuf dependencies |
| `services/serializer/internal/serializer/ids.go` | Create | `AssignIDs` — deterministic short ID map |
| `services/serializer/internal/serializer/ids_test.go` | Create | ID assignment tests |
| `services/serializer/internal/serializer/layers/header.go` | Create | Layer 1: QUERY / SEEDS / CONFIDENCE / REPO |
| `services/serializer/internal/serializer/layers/header_test.go` | Create | Header rendering tests |
| `services/serializer/internal/serializer/layers/warnings.go` | Create | Layer 2: CONFLICT / DEAD / STALE blocks |
| `services/serializer/internal/serializer/layers/warnings_test.go` | Create | Warning rendering tests |
| `services/serializer/internal/serializer/layers/registry.go` | Create | Layer 3: one line per node |
| `services/serializer/internal/serializer/layers/registry_test.go` | Create | Registry rendering tests |
| `services/serializer/internal/serializer/layers/edges.go` | Create | Layer 4: edges grouped by type |
| `services/serializer/internal/serializer/layers/edges_test.go` | Create | Edge rendering tests |
| `services/serializer/internal/serializer/layers/behaviour.go` | Create | Layer 5: PATH or BODY per seed/hop-1 node |
| `services/serializer/internal/serializer/layers/behaviour_test.go` | Create | Behaviour rendering tests |
| `services/serializer/internal/serializer/layers/sideeffects.go` | Create | Layer 6: SIDE_EFFECTS per node |
| `services/serializer/internal/serializer/layers/sideeffects_test.go` | Create | Side effects rendering tests |
| `services/serializer/internal/serializer/serializer.go` | Create | `Serializer` — orchestrate all six layers |
| `services/serializer/internal/serializer/serializer_test.go` | Create | End-to-end render test with mock subgraph |
| `services/serializer/internal/server/server.go` | Create | gRPC `QueryService/Serialize` handler |
| `services/serializer/internal/server/server_test.go` | Create | Server handler tests |
| `services/serializer/cmd/main.go` | Create | Entry point — gRPC + HTTP servers, shutdown |
| `services/serializer/Dockerfile` | Create | Multi-stage Go build |
| `docker-compose.yml` | Modify | Add `serializer` service |

---

## Task 1: Update proto, Makefile, and generate stubs

**Files:**
- Modify: `proto/query.proto`
- Modify: `Makefile`
- Generate: `services/serializer/gen/`

- [ ] **Step 1: Add `repo` to SerializeRequest**

In `proto/query.proto`, replace the `SerializeRequest` message:

```protobuf
message SerializeRequest {
  RankedSubgraphResponse subgraph = 1;
  QueryIntentResponse    intent   = 2;
  string repo = 3;
}
```

- [ ] **Step 2: Update Makefile to generate serializer stubs**

In `Makefile`, add `services/serializer/gen` to the gen targets. Replace the `proto:` block:

```makefile
PROTO_DIR     := proto
GO_OUT        := services/dual-retriever/gen
SERIALIZER_GO_OUT := services/serializer/gen
PYTHON_OUT    := services/query-understander/gen

proto:
	mkdir -p $(GO_OUT) $(SERIALIZER_GO_OUT) $(PYTHON_OUT)
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(GO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(GO_OUT) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/*.proto
	protoc \
		--proto_path=$(PROTO_DIR) \
		--go_out=$(SERIALIZER_GO_OUT) --go_opt=paths=source_relative \
		--go-grpc_out=$(SERIALIZER_GO_OUT) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/*.proto
	python -m grpc_tools.protoc \
		--proto_path=$(PROTO_DIR) \
		--python_out=$(PYTHON_OUT) \
		--grpc_python_out=$(PYTHON_OUT) \
		$(PROTO_DIR)/*.proto
```

- [ ] **Step 3: Regenerate all stubs**

> **Note on `go_package`:** The proto file declares `go_package = ".../dual-retriever/gen;querypb"`.
> With `paths=source_relative`, protoc ignores that path for output placement — it just mirrors
> the proto source path. The generated files declare `package querypb` with no embedded import
> path in the Go source. The Go compiler resolves the import path from `go.mod` + directory
> (`github.com/tersecontext/tc/services/serializer/gen`), so all imports compile correctly.
> The `dual-retriever` string only appears in the binary proto descriptor blob (runtime reflection) —
> it does not affect compilation.

Run: `make proto`

Expected: `services/serializer/gen/query.pb.go` and `query_grpc.pb.go` created. `services/dual-retriever/gen/` regenerated with the new `repo` field on `SerializeRequest`.

- [ ] **Step 4: Add grpc and protobuf dependencies to serializer go.mod**

Run:
```bash
cd services/serializer
go get google.golang.org/grpc@v1.79.3
go get google.golang.org/protobuf@v1.36.11
go get google.golang.org/grpc/health
go mod tidy
```

Expected: `go.mod` and `go.sum` created/updated.

- [ ] **Step 5: Verify generated code compiles**

Run: `cd services/serializer && go build ./gen/...`

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add proto/query.proto Makefile services/serializer/gen/ services/serializer/go.mod services/serializer/go.sum services/dual-retriever/gen/
git commit -m "feat(serializer): add repo to SerializeRequest, add serializer gen target"
```

---

## Task 2: Short ID assignment

**Files:**
- Create: `services/serializer/internal/serializer/ids.go`
- Create: `services/serializer/internal/serializer/ids_test.go`

- [ ] **Step 1: Write the failing test**

Create `services/serializer/internal/serializer/ids_test.go`:

```go
package serializer_test

import (
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
)

func TestAssignIDs_deterministic(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids1 := serializer.AssignIDs(nodes)
	ids2 := serializer.AssignIDs(nodes)
	if ids1["sha256:fn3"] != ids2["sha256:fn3"] {
		t.Errorf("non-deterministic: got %q then %q", ids1["sha256:fn3"], ids2["sha256:fn3"])
	}
}

func TestAssignIDs_seedFirst(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids := serializer.AssignIDs(nodes)
	if ids["sha256:fn3"] != "fn:1" {
		t.Errorf("seed node: want fn:1, got %q", ids["sha256:fn3"])
	}
	if ids["sha256:fn5"] != "fn:2" {
		t.Errorf("hop-1 node: want fn:2, got %q", ids["sha256:fn5"])
	}
}

func TestAssignIDs_multipleTypes(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3",  Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:cls1", Type: "class",    Hop: 1, Score: 0.8},
		{StableId: "sha256:fn5",  Type: "function", Hop: 1, Score: 0.7},
	}
	ids := serializer.AssignIDs(nodes)
	if ids["sha256:fn3"] != "fn:1"  { t.Errorf("want fn:1,  got %q", ids["sha256:fn3"]) }
	if ids["sha256:fn5"] != "fn:2"  { t.Errorf("want fn:2,  got %q", ids["sha256:fn5"]) }
	if ids["sha256:cls1"] != "cls:1" { t.Errorf("want cls:1, got %q", ids["sha256:cls1"]) }
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/... -run TestAssignIDs -v`

Expected: compile error — package does not exist yet.

- [ ] **Step 3: Implement `ids.go`**

Create `services/serializer/internal/serializer/ids.go`:

```go
package serializer

import (
	"fmt"
	"sort"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// AssignIDs returns a map from node StableId to short display ID.
// IDs are deterministic: within each type group, nodes are sorted
// by hop (ascending) then score (descending).
func AssignIDs(nodes []*querypb.SubgraphNode) map[string]string {
	byType := map[string][]*querypb.SubgraphNode{}
	for _, n := range nodes {
		byType[n.Type] = append(byType[n.Type], n)
	}

	prefixes := map[string]string{
		"function": "fn",
		"method":   "fn",
		"class":    "cls",
		"file":     "file",
	}
	// counters are per-prefix (not per-type), so function and method share "fn" counter
	counters := map[string]int{}
	ids := map[string]string{}

	// Process types in stable order so the full ID map is deterministic
	typeOrder := []string{"function", "method", "class", "file"}
	for _, nodeType := range typeOrder {
		group, ok := byType[nodeType]
		if !ok {
			continue
		}
		sort.Slice(group, func(i, j int) bool {
			if group[i].Hop != group[j].Hop {
				return group[i].Hop < group[j].Hop
			}
			return group[i].Score > group[j].Score
		})
		prefix := prefixes[nodeType]
		for _, n := range group {
			counters[prefix]++
			ids[n.StableId] = fmt.Sprintf("%s:%d", prefix, counters[prefix])
		}
	}
	return ids
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/... -run TestAssignIDs -v`

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/ids.go services/serializer/internal/serializer/ids_test.go
git commit -m "feat(serializer): add deterministic short ID assignment"
```

---

## Task 3: Layer 1 — Header

**Files:**
- Create: `services/serializer/internal/serializer/layers/header.go`
- Create: `services/serializer/internal/serializer/layers/header_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/header_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderHeader_basic(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 23, BranchCoverage: 0.75},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderHeader(&buf, "how does auth work", "myrepo", nodes, nil, ids)
	out := buf.String()

	checks := []string{
		"QUERY:",
		"how does auth work",
		"SEEDS:",
		"fn:1",
		"*",
		"CONFIDENCE:",
		"medium",
		"REPO:",
		"myrepo",
	}
	for _, want := range checks {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in header output:\n%s", want, out)
		}
	}
}

func TestRenderHeader_highConfidence(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 15, BranchCoverage: 0.9},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, nil, ids)
	if !strings.Contains(buf.String(), "high") {
		t.Errorf("expected high confidence")
	}
}

func TestRenderHeader_lowConfidence_warnings(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 0},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, warnings, ids)
	if !strings.Contains(buf.String(), "low") {
		t.Errorf("expected low confidence")
	}
}

func TestRenderHeader_multipleSeeds(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:a", Type: "function", Hop: 0, Score: 1.0},
		{StableId: "sha256:b", Type: "function", Hop: 0, Score: 0.9},
		{StableId: "sha256:c", Type: "function", Hop: 1, Score: 0.5},
	}
	ids := map[string]string{
		"sha256:a": "fn:1",
		"sha256:b": "fn:2",
		"sha256:c": "fn:3",
	}
	var buf strings.Builder
	layers.RenderHeader(&buf, "q", "r", nodes, nil, ids)
	out := buf.String()
	// both seeds appear, hop-1 node does not appear in SEEDS line
	if !strings.Contains(out, "fn:1") || !strings.Contains(out, "fn:2") {
		t.Errorf("both seeds should appear: %s", out)
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderHeader -v`

Expected: compile error.

- [ ] **Step 3: Implement `header.go`**

Create `services/serializer/internal/serializer/layers/header.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderHeader writes Layer 1 (query header) to buf.
func RenderHeader(
	buf *strings.Builder,
	rawQuery, repo string,
	nodes []*querypb.SubgraphNode,
	warnings []*querypb.SubgraphWarning,
	ids map[string]string,
) {
	// Collect seed nodes (hop == 0), in ID order
	var seeds []*querypb.SubgraphNode
	for _, n := range nodes {
		if n.Hop == 0 {
			seeds = append(seeds, n)
		}
	}

	// Build SEEDS line
	var seedParts []string
	for _, s := range seeds {
		id := ids[s.StableId]
		seedParts = append(seedParts, id+" *")
	}
	seedsLine := strings.Join(seedParts, "  ")

	// Determine confidence from first seed
	conf, runs, coverage := confidence(seeds, warnings)

	fmt.Fprintf(buf, "QUERY:       %s\n", rawQuery)
	fmt.Fprintf(buf, "SEEDS:       %s\n", seedsLine)
	fmt.Fprintf(buf, "CONFIDENCE:  %s  (%d observed runs · %.0f%% coverage · %d warnings)\n",
		conf, runs, coverage*100, len(warnings))
	fmt.Fprintf(buf, "REPO:        %s\n", repo)
	buf.WriteString("\n")
}

func confidence(seeds []*querypb.SubgraphNode, warnings []*querypb.SubgraphWarning) (string, int32, float64) {
	if len(seeds) == 0 {
		return "low", 0, 0
	}
	seed := seeds[0]
	if len(warnings) > 0 || seed.ObservedCalls == 0 {
		return "low", seed.ObservedCalls, seed.BranchCoverage
	}
	if seed.ObservedCalls >= 10 && seed.BranchCoverage >= 0.8 {
		return "high", seed.ObservedCalls, seed.BranchCoverage
	}
	return "medium", seed.ObservedCalls, seed.BranchCoverage
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderHeader -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/header.go services/serializer/internal/serializer/layers/header_test.go
git commit -m "feat(serializer): add Layer 1 header renderer"
```

---

## Task 4: Layer 2 — Warnings

**Files:**
- Create: `services/serializer/internal/serializer/layers/warnings.go`
- Create: `services/serializer/internal/serializer/layers/warnings_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/warnings_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderWarnings_conflict(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "CONFLICT", Source: "sha256:fn3", Target: "sha256:fn5",
			Detail: "static: always called · observed: 0/10 runs"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "CONFLICT") { t.Errorf("missing CONFLICT: %s", out) }
	if !strings.Contains(out, "fn:1") { t.Errorf("missing source id: %s", out) }
	if !strings.Contains(out, "fn:2") { t.Errorf("missing target id: %s", out) }
	if !strings.Contains(out, "static: always called") { t.Errorf("missing detail: %s", out) }
}

func TestRenderWarnings_dead(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5", Detail: "in AST · never observed"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "DEAD") { t.Errorf("missing DEAD: %s", out) }
}

func TestRenderWarnings_stale(t *testing.T) {
	warnings := []*querypb.SubgraphWarning{
		{Type: "STALE", Source: "sha256:fn3", Detail: "spec last run 45 days ago"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderWarnings(&buf, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "STALE") { t.Errorf("missing STALE: %s", out) }
	if !strings.Contains(out, "fn:1") { t.Errorf("missing id: %s", out) }
}

func TestRenderWarnings_empty(t *testing.T) {
	var buf strings.Builder
	layers.RenderWarnings(&buf, nil, nil)
	if buf.Len() != 0 {
		t.Errorf("expected empty output for no warnings, got: %q", buf.String())
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderWarnings -v`

Expected: compile error.

- [ ] **Step 3: Implement `warnings.go`**

Create `services/serializer/internal/serializer/layers/warnings.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderWarnings writes Layer 2 (warnings) to buf.
// Writes nothing if warnings is empty.
func RenderWarnings(buf *strings.Builder, warnings []*querypb.SubgraphWarning, ids map[string]string) {
	if len(warnings) == 0 {
		return
	}
	for _, w := range warnings {
		srcID := ids[w.Source]
		switch w.Type {
		case "CONFLICT":
			tgtID := ids[w.Target]
			fmt.Fprintf(buf, "CONFLICT  %s -> %s\n", srcID, tgtID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		case "DEAD":
			tgtID := ids[w.Target]
			fmt.Fprintf(buf, "DEAD      %s -> %s\n", srcID, tgtID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		case "STALE":
			fmt.Fprintf(buf, "STALE     %s\n", srcID)
			fmt.Fprintf(buf, "          %s\n", w.Detail)
		}
	}
	buf.WriteString("\n")
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderWarnings -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/warnings.go services/serializer/internal/serializer/layers/warnings_test.go
git commit -m "feat(serializer): add Layer 2 warnings renderer"
```

---

## Task 5: Layer 3 — Node Registry

**Files:**
- Create: `services/serializer/internal/serializer/layers/registry.go`
- Create: `services/serializer/internal/serializer/layers/registry_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/registry_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderRegistry_onePerNode(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "authenticate(user: User, pw: str) -> Token",
			ObservedCalls: 23, BranchCoverage: 0.75},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Signature: "_hash_password(pw: str, salt: bytes) -> str",
			Provenance: "confirmed"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	lines := strings.Split(strings.TrimSpace(out), "\n")
	// header line + 2 node lines + blank line separator
	if len(lines) < 2 {
		t.Fatalf("expected at least 2 lines, got %d:\n%s", len(lines), out)
	}
	if !strings.Contains(out, "fn:1") { t.Errorf("missing fn:1: %s", out) }
	if !strings.Contains(out, "fn:2") { t.Errorf("missing fn:2: %s", out) }
	if !strings.Contains(out, "authenticate") { t.Errorf("missing signature: %s", out) }
}

func TestRenderRegistry_seedMarked(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "foo()", ObservedCalls: 5},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	// seed node: id should appear with *
	if !strings.Contains(out, "fn:1 *") {
		t.Errorf("seed node should have * marker: %s", out)
	}
}

func TestRenderRegistry_provenancePills(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Signature: "a()", ObservedCalls: 10, BranchCoverage: 0.8},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Signature: "b()", Provenance: "confirmed"},
		{StableId: "sha256:fn6", Type: "function", Hop: 1, Score: 0.5,
			Signature: "c()", Provenance: "runtime-only"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
		"sha256:fn6": "fn:3",
	}
	var buf strings.Builder
	layers.RenderRegistry(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "spec.80%") { t.Errorf("missing spec pill: %s", out) }
	if !strings.Contains(out, "static")    { t.Errorf("missing static pill: %s", out) }
	if !strings.Contains(out, "runtime-only") { t.Errorf("missing runtime-only pill: %s", out) }
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderRegistry -v`

Expected: compile error.

- [ ] **Step 3: Implement `registry.go`**

Create `services/serializer/internal/serializer/layers/registry.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderRegistry writes Layer 3 (node registry) to buf.
func RenderRegistry(buf *strings.Builder, nodes []*querypb.SubgraphNode, ids map[string]string) {
	for _, n := range nodes {
		id := ids[n.StableId]
		idCol := id
		if n.Hop == 0 {
			idCol = id + " *"
		}
		sig := n.Signature
		if sig == "" {
			sig = n.Name
		}
		pill := provenancePill(n)
		fmt.Fprintf(buf, "%-12s  %-50s  %s\n", idCol, sig, pill)
	}
	buf.WriteString("\n")
}

func provenancePill(n *querypb.SubgraphNode) string {
	if n.Provenance == "runtime-only" {
		return "runtime-only"
	}
	if n.ObservedCalls > 0 || n.BranchCoverage > 0 {
		pct := int(n.BranchCoverage * 100)
		return fmt.Sprintf("spec.%d%%", pct)
	}
	return "static"
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderRegistry -v`

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/registry.go services/serializer/internal/serializer/layers/registry_test.go
git commit -m "feat(serializer): add Layer 3 node registry renderer"
```

---

## Task 6: Layer 4 — Edge Topology

**Files:**
- Create: `services/serializer/internal/serializer/layers/edges.go`
- Create: `services/serializer/internal/serializer/layers/edges_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/edges_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderEdges_groupsByType(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
		{Source: "sha256:fn5", Target: "sha256:fn6", Type: "CALLS", Provenance: "confirmed"},
		{Source: "sha256:fn3", Target: "sha256:file1", Type: "IMPORTS", Provenance: "confirmed"},
	}
	ids := map[string]string{
		"sha256:fn3":   "fn:1",
		"sha256:fn5":   "fn:2",
		"sha256:fn6":   "fn:3",
		"sha256:file1": "file:1",
	}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "CALLS:") { t.Errorf("missing CALLS: section: %s", out) }
	if !strings.Contains(out, "IMPORTS:") { t.Errorf("missing IMPORTS: section: %s", out) }
	if !strings.Contains(out, "fn:1 -> fn:2") { t.Errorf("missing fn:1 -> fn:2: %s", out) }
	if !strings.Contains(out, "fn:1 -> file:1") { t.Errorf("missing import edge: %s", out) }
}

func TestRenderEdges_runtimeOnlyTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "runtime-only"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "[runtime-only]") {
		t.Errorf("expected [runtime-only] tag: %s", out)
	}
}

func TestRenderEdges_conflictTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "CONFLICT", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, warnings, ids)
	out := buf.String()
	if !strings.Contains(out, "[CONFLICT]") {
		t.Errorf("expected [CONFLICT] tag: %s", out)
	}
}

func TestRenderEdges_deadTag(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
	}
	warnings := []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderEdges(&buf, edges, warnings, ids)
	if !strings.Contains(buf.String(), "[DEAD]") {
		t.Errorf("expected [DEAD] tag: %s", buf.String())
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderEdges -v`

Expected: compile error.

- [ ] **Step 3: Implement `edges.go`**

Create `services/serializer/internal/serializer/layers/edges.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderEdges writes Layer 4 (edge topology) to buf, grouped by edge type.
func RenderEdges(
	buf *strings.Builder,
	edges []*querypb.SubgraphEdge,
	warnings []*querypb.SubgraphWarning,
	ids map[string]string,
) {
	// Build lookup: (source, target) -> warning type
	warnKey := func(src, tgt string) string { return src + "→" + tgt }
	warnMap := map[string]string{}
	for _, w := range warnings {
		if w.Type == "CONFLICT" || w.Type == "DEAD" {
			warnMap[warnKey(w.Source, w.Target)] = w.Type
		}
	}

	// Group edges by type, preserving order of first appearance
	seen := map[string]bool{}
	var typeOrder []string
	byType := map[string][]*querypb.SubgraphEdge{}
	for _, e := range edges {
		if !seen[e.Type] {
			seen[e.Type] = true
			typeOrder = append(typeOrder, e.Type)
		}
		byType[e.Type] = append(byType[e.Type], e)
	}

	for _, edgeType := range typeOrder {
		fmt.Fprintf(buf, "%s:\n", edgeType)
		for _, e := range byType[edgeType] {
			srcID := ids[e.Source]
			tgtID := ids[e.Target]
			tag := ""
			if e.Provenance == "runtime-only" {
				tag = "  [runtime-only]"
			} else if wt, ok := warnMap[warnKey(e.Source, e.Target)]; ok {
				tag = fmt.Sprintf("  [%s]        see warnings", wt)
			}
			fmt.Fprintf(buf, "  %s -> %s%s\n", srcID, tgtID, tag)
		}
	}
	buf.WriteString("\n")
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderEdges -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/edges.go services/serializer/internal/serializer/layers/edges_test.go
git commit -m "feat(serializer): add Layer 4 edge topology renderer"
```

---

## Task 7: Layer 5 — Behaviour

**Files:**
- Create: `services/serializer/internal/serializer/layers/behaviour.go`
- Create: `services/serializer/internal/serializer/layers/behaviour_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/behaviour_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderBehaviour_pathForSpec(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS"},
	}
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			ObservedCalls: 23, BehaviorSpec: "1. hash_password   always   12ms"},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Score: 0.7,
			Body: "def _hash_password(pw, salt):\n  return bcrypt.hash(pw, salt)"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
	}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, edges, ids)
	out := buf.String()
	if !strings.Contains(out, "PATH fn:1") { t.Errorf("expected PATH for seed with spec: %s", out) }
	if !strings.Contains(out, "hash_password") { t.Errorf("expected spec content: %s", out) }
	if !strings.Contains(out, "BODY fn:2") { t.Errorf("expected BODY for hop-1 without spec: %s", out) }
	if !strings.Contains(out, "bcrypt") { t.Errorf("expected body content: %s", out) }
}

func TestRenderBehaviour_bodyWhenNoSpec(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
			Body: "def authenticate(user, pw):\n  return token"},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, nil, ids)
	out := buf.String()
	if !strings.Contains(out, "BODY fn:1") { t.Errorf("expected BODY: %s", out) }
	if !strings.Contains(out, "authenticate") { t.Errorf("expected body content: %s", out) }
}

func TestRenderBehaviour_onlySeedsAndHop1(t *testing.T) {
	edges := []*querypb.SubgraphEdge{
		{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS"},
	}
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, Body: "seed body"},
		{StableId: "sha256:fn5", Type: "function", Hop: 1, Body: "hop1 body"},
		{StableId: "sha256:fn6", Type: "function", Hop: 2, Body: "hop2 body — should not appear"},
	}
	ids := map[string]string{
		"sha256:fn3": "fn:1",
		"sha256:fn5": "fn:2",
		"sha256:fn6": "fn:3",
	}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, edges, ids)
	out := buf.String()
	if strings.Contains(out, "hop2 body") {
		t.Errorf("hop-2 node should not appear in behaviour layer: %s", out)
	}
	if !strings.Contains(out, "hop1 body") {
		t.Errorf("hop-1 node should appear: %s", out)
	}
}

func TestRenderBehaviour_emptyWhenNoBody(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderBehaviour(&buf, nodes, nil, ids)
	// node with no spec and no body should still get a BODY header, just empty
	out := buf.String()
	if !strings.Contains(out, "fn:1") {
		t.Errorf("seed node should still appear: %s", out)
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderBehaviour -v`

Expected: compile error.

- [ ] **Step 3: Implement `behaviour.go`**

Create `services/serializer/internal/serializer/layers/behaviour.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderBehaviour writes Layer 5 (behaviour) to buf.
// Includes seed nodes (hop=0) and hop-1 nodes reachable via CALLS from a seed.
func RenderBehaviour(
	buf *strings.Builder,
	nodes []*querypb.SubgraphNode,
	edges []*querypb.SubgraphEdge,
	ids map[string]string,
) {
	// Build set of seed stable IDs
	seedIDs := map[string]bool{}
	for _, n := range nodes {
		if n.Hop == 0 {
			seedIDs[n.StableId] = true
		}
	}

	// Build set of hop-1 nodes directly called by a seed
	hop1Targets := map[string]bool{}
	for _, e := range edges {
		if e.Type == "CALLS" && seedIDs[e.Source] {
			hop1Targets[e.Target] = true
		}
	}

	// Index nodes by stable ID
	nodeByID := map[string]*querypb.SubgraphNode{}
	for _, n := range nodes {
		nodeByID[n.StableId] = n
	}

	// Render seeds first, then hop-1 callees
	rendered := map[string]bool{}
	render := func(n *querypb.SubgraphNode) {
		if rendered[n.StableId] {
			return
		}
		rendered[n.StableId] = true
		id := ids[n.StableId]
		if n.BehaviorSpec != "" {
			seedMark := ""
			if n.Hop == 0 {
				seedMark = fmt.Sprintf(" (* seed · %d runs observed)", n.ObservedCalls)
			}
			fmt.Fprintf(buf, "PATH %s%s\n", id, seedMark)
			for _, line := range strings.Split(n.BehaviorSpec, "\n") {
				fmt.Fprintf(buf, "  %s\n", line)
			}
		} else {
			seedMark := ""
			if n.Hop == 0 {
				seedMark = " (* static · no spec)"
			}
			fmt.Fprintf(buf, "BODY %s%s\n", id, seedMark)
			for _, line := range strings.Split(n.Body, "\n") {
				fmt.Fprintf(buf, "  %s\n", line)
			}
		}
		buf.WriteString("\n")
	}

	for _, n := range nodes {
		if n.Hop == 0 {
			render(n)
		}
	}
	for _, n := range nodes {
		if hop1Targets[n.StableId] {
			render(n)
		}
	}
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderBehaviour -v`

Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/behaviour.go services/serializer/internal/serializer/layers/behaviour_test.go
git commit -m "feat(serializer): add Layer 5 behaviour renderer"
```

---

## Task 8: Layer 6 — Side Effects

**Files:**
- Create: `services/serializer/internal/serializer/layers/sideeffects.go`
- Create: `services/serializer/internal/serializer/layers/sideeffects_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/serializer/layers/sideeffects_test.go`:

```go
package layers_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

func TestRenderSideEffects_present(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0,
			SideEffects: []string{
				"DB READ users WHERE id=$1",
				"CACHE SET session:{id} TTL 3600",
			},
		},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "SIDE_EFFECTS fn:1") { t.Errorf("missing header: %s", out) }
	if !strings.Contains(out, "DB READ") { t.Errorf("missing db read: %s", out) }
	if !strings.Contains(out, "CACHE SET") { t.Errorf("missing cache set: %s", out) }
}

func TestRenderSideEffects_empty(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", Type: "function", Hop: 0, SideEffects: nil},
	}
	ids := map[string]string{"sha256:fn3": "fn:1"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	if buf.Len() != 0 {
		t.Errorf("expected empty output when no side effects, got: %q", buf.String())
	}
}

func TestRenderSideEffects_multipleNodes(t *testing.T) {
	nodes := []*querypb.SubgraphNode{
		{StableId: "sha256:fn3", SideEffects: []string{"DB READ users"}},
		{StableId: "sha256:fn5", SideEffects: []string{"HTTP OUT POST /api/notify"}},
	}
	ids := map[string]string{"sha256:fn3": "fn:1", "sha256:fn5": "fn:2"}
	var buf strings.Builder
	layers.RenderSideEffects(&buf, nodes, ids)
	out := buf.String()
	if !strings.Contains(out, "fn:1") { t.Errorf("missing fn:1: %s", out) }
	if !strings.Contains(out, "fn:2") { t.Errorf("missing fn:2: %s", out) }
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderSideEffects -v`

Expected: compile error.

- [ ] **Step 3: Implement `sideeffects.go`**

Create `services/serializer/internal/serializer/layers/sideeffects.go`:

```go
package layers

import (
	"fmt"
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
)

// RenderSideEffects writes Layer 6 (side effects) to buf.
// Writes nothing if no nodes have side effects.
func RenderSideEffects(buf *strings.Builder, nodes []*querypb.SubgraphNode, ids map[string]string) {
	for _, n := range nodes {
		if len(n.SideEffects) == 0 {
			continue
		}
		id := ids[n.StableId]
		fmt.Fprintf(buf, "SIDE_EFFECTS %s:\n", id)
		for _, effect := range n.SideEffects {
			fmt.Fprintf(buf, "  %s\n", effect)
		}
		buf.WriteString("\n")
	}
}
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `cd services/serializer && go test ./internal/serializer/layers/... -run TestRenderSideEffects -v`

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/layers/sideeffects.go services/serializer/internal/serializer/layers/sideeffects_test.go
git commit -m "feat(serializer): add Layer 6 side effects renderer"
```

---

## Task 9: Serializer Orchestrator

**Files:**
- Create: `services/serializer/internal/serializer/serializer.go`
- Create: `services/serializer/internal/serializer/serializer_test.go`

- [ ] **Step 1: Write the failing integration test**

Create `services/serializer/internal/serializer/serializer_test.go`:

```go
package serializer_test

import (
	"strings"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
)

func mockRequest() *querypb.SerializeRequest {
	return &querypb.SerializeRequest{
		Subgraph: &querypb.RankedSubgraphResponse{
			Nodes: []*querypb.SubgraphNode{
				{
					StableId:       "sha256:fn3",
					Name:           "authenticate",
					Type:           "function",
					Signature:      "authenticate(user: User, pw: str) -> Token",
					Body:           "def authenticate(self, user, pw):\n    ...",
					Score:          1.0,
					Hop:            0,
					Provenance:     "confirmed",
					ObservedCalls:  23,
					AvgLatencyMs:   28,
					BranchCoverage: 0.75,
				},
				{
					StableId:   "sha256:fn5",
					Name:       "_hash_password",
					Type:       "function",
					Signature:  "_hash_password(pw: str, salt: bytes) -> str",
					Score:      0.7,
					Hop:        1,
					Provenance: "confirmed",
				},
			},
			Edges: []*querypb.SubgraphEdge{
				{Source: "sha256:fn3", Target: "sha256:fn5", Type: "CALLS", Provenance: "confirmed"},
			},
		},
		Intent: &querypb.QueryIntentResponse{RawQuery: "how does auth work"},
		Repo:   "test",
	}
}

func TestSerializer_allLayers(t *testing.T) {
	s := serializer.New()
	doc, tokens, err := s.Serialize(mockRequest())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if tokens <= 0 {
		t.Errorf("expected positive token count, got %d", tokens)
	}

	checks := []string{
		"QUERY:",
		"how does auth work",
		"SEEDS:",
		"fn:1",
		"CONFIDENCE:",
		"REPO:",
		"test",
		"fn:1",
		"fn:2",
		"authenticate",
		"CALLS:",
		"fn:1 -> fn:2",
		"BODY fn:1",
	}
	for _, want := range checks {
		if !strings.Contains(doc, want) {
			t.Errorf("missing %q in document:\n%s", want, doc)
		}
	}
}

func TestSerializer_noWarningsLayer(t *testing.T) {
	s := serializer.New()
	req := mockRequest()
	req.Subgraph.Warnings = nil
	doc, _, _ := s.Serialize(req)
	if strings.Contains(doc, "CONFLICT") || strings.Contains(doc, "DEAD") || strings.Contains(doc, "STALE") {
		t.Errorf("Layer 2 should be absent when no warnings: %s", doc)
	}
}

func TestSerializer_warningsLayerPresent(t *testing.T) {
	s := serializer.New()
	req := mockRequest()
	req.Subgraph.Warnings = []*querypb.SubgraphWarning{
		{Type: "DEAD", Source: "sha256:fn3", Target: "sha256:fn5", Detail: "never observed"},
	}
	doc, _, _ := s.Serialize(req)
	if !strings.Contains(doc, "DEAD") {
		t.Errorf("Layer 2 should be present when warnings exist: %s", doc)
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/serializer/... -run TestSerializer -v`

Expected: compile error.

- [ ] **Step 3: Implement `serializer.go`**

Create `services/serializer/internal/serializer/serializer.go`:

```go
package serializer

import (
	"strings"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer/layers"
)

// Serializer renders a RankedSubgraph into a six-layer context document.
type Serializer struct{}

func New() *Serializer { return &Serializer{} }

// Serialize returns the rendered document, an estimated token count, and any error.
func (s *Serializer) Serialize(req *querypb.SerializeRequest) (string, int32, error) {
	sg := req.GetSubgraph()
	nodes := sg.GetNodes()
	edges := sg.GetEdges()
	warnings := sg.GetWarnings()
	rawQuery := req.GetIntent().GetRawQuery()
	repo := req.GetRepo()

	ids := AssignIDs(nodes)

	var buf strings.Builder

	// Layer 1 — always present
	layers.RenderHeader(&buf, rawQuery, repo, nodes, warnings, ids)

	// Layer 2 — omit if no warnings
	if len(warnings) > 0 {
		layers.RenderWarnings(&buf, warnings, ids)
	}

	// Layer 3 — always present
	layers.RenderRegistry(&buf, nodes, ids)

	// Layer 4 — always present
	layers.RenderEdges(&buf, edges, warnings, ids)

	// Layer 5 — always present (seeds + hop-1 callees)
	layers.RenderBehaviour(&buf, nodes, edges, ids)

	// Layer 6 — omit if no side effects
	hasSideEffects := false
	for _, n := range nodes {
		if len(n.SideEffects) > 0 {
			hasSideEffects = true
			break
		}
	}
	if hasSideEffects {
		layers.RenderSideEffects(&buf, nodes, ids)
	}

	doc := buf.String()
	return doc, estimateTokens(doc), nil
}

// estimateTokens returns a rough token count (chars / 4).
func estimateTokens(doc string) int32 {
	return int32(len(doc) / 4)
}
```

- [ ] **Step 4: Run all serializer tests**

Run: `cd services/serializer && go test ./internal/serializer/... -v`

Expected: all tests PASS (IDs, all layers, orchestrator).

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/serializer/serializer.go services/serializer/internal/serializer/serializer_test.go
git commit -m "feat(serializer): add Serializer orchestrator"
```

---

## Task 10: gRPC Server Handler

**Files:**
- Create: `services/serializer/internal/server/server.go`
- Create: `services/serializer/internal/server/server_test.go`

- [ ] **Step 1: Write the failing tests**

Create `services/serializer/internal/server/server_test.go`:

```go
package server_test

import (
	"context"
	"testing"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/server"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestSerialize_success(t *testing.T) {
	srv := server.NewQueryServer()
	req := &querypb.SerializeRequest{
		Subgraph: &querypb.RankedSubgraphResponse{
			Nodes: []*querypb.SubgraphNode{
				{StableId: "sha256:fn3", Type: "function", Hop: 0, Score: 1.0,
					Signature: "foo()", Name: "foo"},
			},
		},
		Intent: &querypb.QueryIntentResponse{RawQuery: "what is foo"},
		Repo:   "test",
	}
	resp, err := srv.Serialize(context.Background(), req)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.Document == "" {
		t.Error("expected non-empty document")
	}
	if resp.TokenCount <= 0 {
		t.Error("expected positive token count")
	}
}

func TestSerialize_nilSubgraph(t *testing.T) {
	srv := server.NewQueryServer()
	req := &querypb.SerializeRequest{
		Intent: &querypb.QueryIntentResponse{RawQuery: "q"},
		Repo:   "r",
	}
	_, err := srv.Serialize(context.Background(), req)
	if err == nil {
		t.Fatal("expected error for nil subgraph")
	}
	if status.Code(err) != codes.InvalidArgument {
		t.Errorf("expected InvalidArgument, got %v", status.Code(err))
	}
}

func TestUnimplementedMethods(t *testing.T) {
	srv := server.NewQueryServer()
	ctx := context.Background()
	if _, err := srv.Understand(ctx, &querypb.UnderstandRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Understand: expected Unimplemented, got %v", err)
	}
	if _, err := srv.Retrieve(ctx, &querypb.RetrieveRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Retrieve: expected Unimplemented, got %v", err)
	}
	if _, err := srv.Expand(ctx, &querypb.ExpandRequest{}); status.Code(err) != codes.Unimplemented {
		t.Errorf("Expand: expected Unimplemented, got %v", err)
	}
}
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd services/serializer && go test ./internal/server/... -v`

Expected: compile error.

- [ ] **Step 3: Implement `server.go`**

Create `services/serializer/internal/server/server.go`:

```go
package server

import (
	"context"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/serializer"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type QueryServer struct {
	querypb.UnimplementedQueryServiceServer
	ser *serializer.Serializer
}

func NewQueryServer() *QueryServer {
	return &QueryServer{ser: serializer.New()}
}

func (s *QueryServer) Serialize(ctx context.Context, req *querypb.SerializeRequest) (*querypb.ContextDocResponse, error) {
	if req.GetSubgraph() == nil {
		return nil, status.Error(codes.InvalidArgument, "subgraph is required")
	}
	doc, tokens, err := s.ser.Serialize(req)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "serialize: %v", err)
	}
	return &querypb.ContextDocResponse{Document: doc, TokenCount: tokens}, nil
}

func (s *QueryServer) Understand(_ context.Context, _ *querypb.UnderstandRequest) (*querypb.QueryIntentResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Understand not implemented by serializer")
}

func (s *QueryServer) Retrieve(_ context.Context, _ *querypb.RetrieveRequest) (*querypb.SeedNodesResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Retrieve not implemented by serializer")
}

func (s *QueryServer) Expand(_ context.Context, _ *querypb.ExpandRequest) (*querypb.RankedSubgraphResponse, error) {
	return nil, status.Error(codes.Unimplemented, "Expand not implemented by serializer")
}
```

- [ ] **Step 4: Run all tests**

Run: `cd services/serializer && go test ./... -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/serializer/internal/server/server.go services/serializer/internal/server/server_test.go
git commit -m "feat(serializer): add gRPC server handler"
```

---

## Task 11: Entry Point, go.mod, and Dockerfile

**Files:**
- Create: `services/serializer/cmd/main.go`
- Modify: `services/serializer/go.mod` (finalize with `go mod tidy`)
- Create: `services/serializer/Dockerfile`

- [ ] **Step 1: Create `cmd/main.go`**

Create `services/serializer/cmd/main.go`:

```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"

	querypb "github.com/tersecontext/tc/services/serializer/gen"
	"github.com/tersecontext/tc/services/serializer/internal/server"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func main() {
	port := env("PORT", "8089")
	ctx := context.Background()
	var ready atomic.Bool

	// --- gRPC server ---
	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		slog.Error("failed to listen", "port", port, "error", err)
		os.Exit(1)
	}

	grpcServer := grpc.NewServer()
	querypb.RegisterQueryServiceServer(grpcServer, server.NewQueryServer())

	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	ready.Store(true)
	healthServer.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// --- HTTP health server ---
	httpPort, _ := strconv.Atoi(port)
	httpAddr := fmt.Sprintf(":%d", httpPort+1)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if ready.Load() {
			json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
		} else {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{"status": "not ready"})
		}
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})

	httpServer := &http.Server{Addr: httpAddr, Handler: mux}
	go func() {
		slog.Info("HTTP health server starting", "addr", httpAddr)
		if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
			slog.Error("HTTP server error", "error", err)
		}
	}()

	// --- Graceful shutdown ---
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		sig := <-sigCh
		slog.Info("shutting down", "signal", sig)
		healthServer.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)
		grpcServer.GracefulStop()
		httpServer.Shutdown(ctx)
	}()

	slog.Info("gRPC server starting", "port", port)
	if err := grpcServer.Serve(lis); err != nil {
		slog.Error("gRPC server error", "error", err)
		os.Exit(1)
	}
}
```

- [ ] **Step 2: Tidy go.mod**

Run:
```bash
cd services/serializer
go mod tidy
```

Expected: `go.mod` and `go.sum` finalized.

- [ ] **Step 3: Verify service builds**

Run: `cd services/serializer && go build ./cmd/main.go`

Expected: binary compiles with no errors.

- [ ] **Step 4: Run all tests one final time**

Run: `cd services/serializer && go test ./... -v`

Expected: all tests PASS.

- [ ] **Step 5: Create `Dockerfile`**

Create `services/serializer/Dockerfile`:

```dockerfile
FROM golang:1.23-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o serializer ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates
COPY --from=builder /app/serializer /usr/local/bin/serializer
EXPOSE 8089 8090
CMD ["serializer"]
```

- [ ] **Step 6: Verify Docker build**

Run: `docker build -t serializer-test services/serializer/`

Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add services/serializer/cmd/main.go services/serializer/go.mod services/serializer/go.sum services/serializer/Dockerfile
git commit -m "feat(serializer): add entry point and Dockerfile"
```

---

## Task 12: Wire into docker-compose

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add serializer service to docker-compose**

In `docker-compose.yml`, add the serializer service alongside the other services. Follow the same pattern as `dual-retriever`. The serializer has no external dependencies (no database in this phase), so `depends_on` can be omitted or reference only the network.

Add after the `dual-retriever` block:

```yaml
  serializer:
    build: ./services/serializer
    ports:
      - "8089:8089"
      - "8090:8090"
    networks:
      - tersecontext
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8090/health"]
      interval: 10s
      timeout: 5s
      retries: 3
```

- [ ] **Step 2: Verify docker-compose config is valid**

Run: `docker compose config --quiet`

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(serializer): wire serializer into docker-compose"
```

---

## Definition of Done

- [ ] All six layers render correctly for the mock subgraph in `serializer_test.go`
- [ ] Short IDs are deterministic and consistent within a document
- [ ] Layer 2 omitted when no warnings present
- [ ] CONFLICT and DEAD edges trigger Layer 2 entries and edge tags
- [ ] Layer 5 uses `behavior_spec` text when present, `body` otherwise
- [ ] Token count is positive and reasonable
- [ ] `cd services/serializer && go test ./...` — all pass
- [ ] `docker build services/serializer/` — succeeds
- [ ] `docker compose config --quiet` — valid
