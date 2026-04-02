from __future__ import annotations
import asyncio
import logging
import os
import subprocess
from pathlib import Path

from .languages import detect_languages, get_server_cmd
from .resolver import resolve_stable_id
from .neo4j_writer import write_lsp_edges

logger = logging.getLogger(__name__)


class LSPSession:
    """Manages a single language server subprocess lifecycle."""

    def __init__(self, server_cmd: list[str], workspace_root: str):
        self._cmd = server_cmd
        self._root = workspace_root
        self._proc: subprocess.Popen | None = None
        self._endpoint = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=self._root,
        )
        try:
            from pylsp_jsonrpc.dispatchers import MethodDispatcher
            from pylsp_jsonrpc.endpoint import Endpoint
            from pylsp_jsonrpc.streams import JsonRpcStreamReader, JsonRpcStreamWriter
            reader = JsonRpcStreamReader(self._proc.stdout)
            writer = JsonRpcStreamWriter(self._proc.stdin)
            self._endpoint = Endpoint(MethodDispatcher(), writer.write)
            reader.listen(self._endpoint.consume)

            self._endpoint.request("initialize", {
                "rootUri": Path(self._root).as_uri(),
                "capabilities": {"textDocument": {"callHierarchy": {"dynamicRegistration": False}}},
            }).result(timeout=30)
            self._endpoint.notify("initialized", {})
        except ImportError:
            logger.warning("pylsp-jsonrpc not available; LSP session will be a no-op")
            self._endpoint = None

    def open_file(self, file_path: str, content: str) -> None:
        if not self._endpoint:
            return
        self._endpoint.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": Path(file_path).as_uri(),
                "languageId": self._lang_id(file_path),
                "version": 1,
                "text": content,
            }
        })

    def call_hierarchy_outgoing(self, file_path: str, line: int, character: int) -> list[dict]:
        if not self._endpoint:
            return []
        uri = Path(file_path).as_uri()
        items = self._endpoint.request("callHierarchy/prepare", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        }).result(timeout=10) or []

        edges = []
        for item in items:
            calls = self._endpoint.request("callHierarchy/outgoingCalls", {
                "item": item
            }).result(timeout=10) or []
            for call in calls:
                to = call.get("to", {})
                edges.append({
                    "file": to.get("uri", "").replace("file://", ""),
                    "line": to.get("range", {}).get("start", {}).get("line", 0),
                })
        return edges

    def shutdown(self) -> None:
        if self._endpoint:
            try:
                self._endpoint.request("shutdown", {}).result(timeout=5)
                self._endpoint.notify("exit", {})
            except Exception:
                pass
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    @staticmethod
    def _lang_id(file_path: str) -> str:
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        return {"py": "python", "go": "go", "ts": "typescript", "js": "javascript"}.get(ext, "plaintext")


async def index_repo(repo: str, repo_path: str, language_servers: dict[str, list[str]], driver) -> int:
    """Run LSP indexing for all detected languages in repo_path.
    Blocking LSP/subprocess work is offloaded to a thread pool via run_in_executor.
    Returns total count of edges written."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _index_repo_sync, repo, repo_path, language_servers, driver)


def _index_repo_sync(repo: str, repo_path: str, language_servers: dict[str, list[str]], driver) -> int:
    """Synchronous implementation — called from thread pool by index_repo."""
    source_files = [
        str(p.relative_to(repo_path))
        for p in Path(repo_path).rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.parts)
    ]
    langs = detect_languages(source_files)
    total_edges = 0

    for ext in langs:
        cmd = get_server_cmd(language_servers, ext)
        if cmd is None:
            logger.debug("No server configured for .%s, skipping", ext)
            continue

        session = LSPSession(cmd, repo_path)
        try:
            session.start()
            logger.info("LSP session started: ext=%s repo=%s", ext, repo)

            lang_files = [f for f in source_files if f.endswith(f".{ext}")]
            for rel_path in lang_files:
                abs_path = os.path.join(repo_path, rel_path)
                try:
                    content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                    session.open_file(abs_path, content)
                except Exception as exc:
                    logger.warning("Failed to open %s: %s", rel_path, exc)

            edges: list[tuple[str, str]] = []
            for rel_path in lang_files:
                abs_path = os.path.join(repo_path, rel_path)
                try:
                    content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
                    lines = content.splitlines()
                    for line_num, _ in enumerate(lines):
                        calls = session.call_hierarchy_outgoing(abs_path, line_num, 0)
                        for call in calls:
                            src_id = resolve_stable_id(driver, repo, rel_path, line_num)
                            tgt_file = call["file"]
                            # Strip absolute repo_path prefix to get repo-relative path
                            if tgt_file.startswith(repo_path):
                                tgt_file = tgt_file[len(repo_path):].lstrip("/")
                            tgt_id = resolve_stable_id(driver, repo, tgt_file, call["line"])
                            if src_id and tgt_id and src_id != tgt_id:
                                edges.append((src_id, tgt_id))
                except Exception as exc:
                    logger.warning("LSP crawl error %s: %s", rel_path, exc)

            total_edges += write_lsp_edges(driver, edges)

        except Exception as exc:
            logger.error("LSP session failed: ext=%s repo=%s error=%s", ext, repo, exc)
        finally:
            session.shutdown()

    logger.info("LSP indexing complete: repo=%s edges=%d", repo, total_edges)
    return total_edges
