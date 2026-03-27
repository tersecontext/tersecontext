# TerseContext

**Minimum Context. Maximum Understanding. For you and for an LLM.**

TerseContext is a code indexing system that produces the smallest, 
highest-confidence context window for LLMs working with codebases.
It combines static analysis (AST/graph) with dynamic analysis 
(runtime traces) to give LLMs exactly what they need — and nothing they don't.

## Status

Pre-alpha. Active development.

## How it works

- Parses your codebase into a knowledge graph using Tree-sitter
- Enriches nodes with observed runtime behavior from your test suite
- Merges static and dynamic signals with provenance tags
- Serializes the minimum sufficient subgraph for any given query

## Coming soon

- [ ] Python parser (Tree-sitter)
- [ ] Neo4j graph writer
- [ ] Qdrant vector writer  
- [ ] Runtime trace instrumentation
- [ ] Query pipeline
- [ ] Serializer

## License

MIT