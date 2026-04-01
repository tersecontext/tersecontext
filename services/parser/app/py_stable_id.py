from __future__ import annotations

import hashlib


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def stable_id(repo: str, file_path: str, node_type: str, qualified_name: str) -> str:
    return _sha256(f"{repo}:{file_path}:{node_type}:{qualified_name}")


def node_hash(name: str, signature: str, body: str) -> str:
    return _sha256(name + signature + body)
