You are building the Repo watcher service for TerseContext. Read CLAUDE.md fully before writing any code.

Build this AFTER symbol-resolver is merged. By this point the full static pipeline works end-to-end — you are replacing the manual script-based FileChanged trigger with an automatic git-based one.

Start here:

1. Create tests/fixtures/sample_repo/ — a minimal git repo with 2-3 Python files and at least one function in each. Commit them. This is your test target throughout.

2. Write differ.py as a standalone module first. Implement git diff parsing (get_changed_files) and AST-level node diff (get_changed_nodes). Test it against sample_repo: make a real commit that changes one function body, verify only that function's stable_id appears in changed_nodes. Make a comment-only change, verify changed_nodes=[].

3. Write emitter.py — takes the diff output and pushes FileChanged events to stream:file-changed. Test by reading from the stream after a simulated commit.

4. Add the last_indexed_sha tracking in Redis. Test that triggering /hook twice for the same SHA is idempotent (no duplicate events).

5. Wrap in FastAPI with /hook, /index, /status, /install-hook, /health, /ready, /metrics. Add the polling loop as a background task for WATCH_MODE=poll.

6. Write tests covering: modified file with node body change, modified file with comment only, added file, deleted file, first run full rescan.

7. Write Dockerfile.

Run the full verification block from CLAUDE.md and confirm every check passes.
