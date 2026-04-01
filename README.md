# TerseContext

**Minimum Context. Maximum Understanding.**

TerseContext is a code indexing system that produces the smallest, highest-confidence context window for LLMs working with codebases. It combines static analysis (AST/graph) with dynamic analysis (runtime traces) to give LLMs exactly what they need — and nothing they don't. Supports **Python** and **Go**.

## What the output looks like

Ask: `"what are the external connections for data inflows"`

~~~
QUERY:       what are the external connections for data inflows
SEEDS:       Fo.1 * Fo.2 * Fo.3 * Fo.4 * Fo.5 * Fo.6 * Fo.7 * Fo.8 * Fo.9 * Fo.10 * Fo.11 * Fo.12 * Fo.13 * Fo.14 *
CONFIDENCE:  medium  (0 observed runs · static only · 0 warnings)
REPO:        getuser

Fo.1  *  func TestExternalDatabase_AllowInboundNetwork(T *testing.T)          static
Fo.2  *  func TestImportData_InboundDataSource(T *testing.T)                  static
Fo.3  *  func TestFeedProvider_InboundSourceRecord(T *testing.T)  string       static
Fo.4  *  func TestExternalMetadata_InboundRecord(T *testing.T)                static
Fo.5  *  func TestDataStream_ExternalIngressConfig(T *testing.T)  string       static
Fo.6  *  func TestAuditableDataExchange_InboundStream(T *testing.T)           static
Fo.7  *  func TestSchemaValidator_CheckInboundRecord(T *testing.T)            static
Fo.8  *  func TestExternalConnector_GetNetworkSources(T *testing.T)  Named    static
Fo.9  *  func TestDataConnector_GetExternalNetwork(T *testing.T)  NamedBool   static
Fo.10 *  func TestRateLimit_ApplyToIngestStream(T *testing.T)                 static
Fo.11 *  func TestExternalFeed_RegisterDataInflow(T *testing.T)               static
Fo.12 *  func TestExternalDB_OpenConnection(T *testing.T)                     static
Fo.13 *  func TestRetry_OnExternalFailure(T *testing.T)                       static
Fo.14 *  func TestExternalCache_TrackInboundReads(T *testing.T)               static

BODY Fo.1 (* static · no spec)
  func TestExternalData_GetExternalNetwork(t *testing.T) {
      actualInput  := t.TempDir()
      actualOutput := t.TempDir()
      profilePath  := profileManager.LoadConfig(t, "external")

      // Try to connect to an external DB - should be denied
      dbClient, err := sql.Open("postgres", os.Getenv("EXTERNAL_DB_DSN"))
      require.NoError(t, err)
      _, err = dbClient.Connect(context.TODO(), actualInput, actualOutput)

      t.Fatal("number of found external network inflows - expected dbClient")
  }
  t.Log("terminal network currently denies %s", strings.TrimSpace(stdout))
~~~

Token count: 487 / 2000 budget

The context doc is plain text — paste it directly before your question in any LLM prompt.

![Example context output](docs/images/example_context.png)

## How it works

![TerseContext pipeline overview](docs/images/overview.svg)



- Parses your codebase into a knowledge graph using Tree-sitter (Python + Go)
- Enriches nodes with observed runtime behaviour from your test suite
- Merges static and dynamic signals with provenance tags (static / spec.N% / runtime-only)
- Serializes the minimum sufficient subgraph for any given query, within a token budget

## What's built

**Static pipeline** — repo-watcher → parser → graph-writer → symbol-resolver → embedder → vector-writer

**Dynamic pipeline** — entrypoint-discoverer → instrumenter → trace-runner → trace-normalizer → graph-enricher → spec-generator

Go dynamic tracing via go-instrumenter + go-trace-runner (runtime via `tracert` binary injection).

**Query pipeline** — query-understander → dual-retriever → subgraph-expander → serializer → api-gateway

## Why these stores

| Store | Static pipeline | Dynamic pipeline | Why |
|-------|----------------|-----------------|-----|
| **Neo4j** | Nodes, CALLS + IMPORTS edges | Runtime edges, confirmed/conflict markers | Code is a graph. Cypher traversals find callers, callees, and dependency chains in one query — a relational join chain cannot. |
| **Qdrant** | Code embeddings (`nodes` collection) | Spec embeddings (`specs` collection) | Semantic search over embeddings. Finds code relevant to a question even when no keyword matches. |
| **Postgres** | — | `behavior_specs` table (versioned, queryable) | Structured records with SQL. Specs need joins, history, and UNIQUE constraints — a document store would fight this. |
| **Redis** | Event streams between services | Job queues + trace event streams | Services communicate through Redis lists (jobs) and streams (events). Fast, ordered, no broker to operate. |

## Web UI

TerseContext ships with a web interface for submitting tasks against indexed repositories. Select a repo, describe the feature or question, choose a questioning mode, and configure which gate questions the system asks before proceeding.

![TerseContext web UI](docs/images/webui.png)

## Quick start

See [usage.md](usage.md) for full setup instructions.

```bash
cp .env.example .env          # fill in passwords
make up                       # start all services
make demo                     # see it working against a bundled sample repo in ~5 minutes

# Or index your own repo
curl -X POST http://localhost:8091/install-hook \
  -H 'Content-Type: application/json' \
  -d '{"repo_path": "/path/to/your/repo"}'

# Then query it
curl -X POST http://localhost:8090/query \
  -H 'Content-Type: application/json' \
  -d '{"repo": "your-repo", "question": "what are the external connections for data inflows"}'
```

## License

MIT