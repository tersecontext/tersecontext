# TerseContext

**Minimum Context. Maximum Understanding.**

TerseContext is a code indexing system that produces the smallest, highest-confidence context window for LLMs working with codebases. It combines static analysis (AST/graph) with dynamic analysis (runtime traces) to give LLMs exactly what they need — and nothing they don't.

## What the output looks like

Ask: `"how does authentication work?"`

~~~
QUERY:       how does authentication work?
SEEDS:       A1 *  A2 *
CONFIDENCE:  high  (47 observed runs · 94% coverage · 0 warnings)
REPO:        acme-api

A1 *  auth.service.authenticate(credentials)    spec.94%
A2 *  auth.service.hash_password(pw, salt)      spec.89%
B1    db.users.get_by_email(email)               static
B2    audit.logger.log(event, user_id)           runtime-only

CALLS:
  A1 -> B1
  A1 -> A2
  A1 -> B2  [runtime-only]

PATH A1 (* seed · 47 runs observed)
  1.  get_by_email      47/47 runs   ~12ms
  2a. hash_password     47/47 runs   ~2ms
  2b. [not found]       3/47 runs    exits early → returns None
  3.  log              44/47 runs   conditional · success path

PATH A2 (* seed · 47 runs observed)
  1.  bcrypt.hashpw     47/47 runs   ~1ms

BODY B1 (* static · no spec)
  def get_by_email(self, email: str) -> Optional[User]:
      return self.session.query(User).filter_by(email=email).first()

SIDE_EFFECTS A1:
  DB READ   users WHERE email = ?
  DB WRITE  audit_log INSERT
  CACHE GET session:{user_id} TTL 3600
~~~

Token count: 312 / 2000 budget

The context doc is plain text — paste it directly before your question in any LLM prompt.

## How it works

- Parses your codebase into a knowledge graph using Tree-sitter (Python + Go)
- Enriches nodes with observed runtime behaviour from your test suite
- Merges static and dynamic signals with provenance tags (static / spec.N% / runtime-only)
- Serializes the minimum sufficient subgraph for any given query, within a token budget

## What's built

**Static pipeline** — repo-watcher → parser → graph-writer → symbol-resolver → embedder → vector-writer

**Dynamic pipeline** — entrypoint-discoverer → instrumenter → trace-runner → trace-normalizer → graph-enricher → spec-generator

Go dynamic tracing via go-instrumenter + go-trace-runner (runtime via `tracert` binary injection).

**Query pipeline** — query-understander → dual-retriever → subgraph-expander → serializer → api-gateway

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
  -d '{"repo": "your-repo", "question": "how does authentication work?"}'
```

## License

MIT