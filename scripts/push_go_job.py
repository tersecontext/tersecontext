#!/usr/bin/env python3
"""Push a Go entrypoint job to Redis for testing."""
import json
import sys
import redis

r = redis.Redis(host="localhost", port=6379)

job = {
    "stable_id": "sha256:test_stable_id",
    "name": sys.argv[1] if len(sys.argv) > 1 else "TestExample",
    "file_path": sys.argv[2] if len(sys.argv) > 2 else "example_test.go",
    "priority": 2,
    "repo": sys.argv[3] if len(sys.argv) > 3 else "test-repo",
    "language": "go",
}

key = f"entrypoint_queue:{job['repo']}:go"
r.rpush(key, json.dumps(job))
print(f"Pushed job to {key}: {json.dumps(job, indent=2)}")
