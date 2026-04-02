from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"py", "go", "ts", "js"}


def detect_languages(file_paths: list[str]) -> set[str]:
    """Return the set of language extensions found in file_paths."""
    langs = set()
    for path in file_paths:
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if ext in SUPPORTED_EXTENSIONS:
            langs.add(ext)
    return langs


def parse_language_servers(config_json: str) -> dict[str, list[str]]:
    """Parse LANGUAGE_SERVERS env var JSON. Returns {} on empty or invalid input."""
    if not config_json:
        return {}
    try:
        data = json.loads(config_json)
        return {k: v for k, v in data.items() if isinstance(v, list)}
    except json.JSONDecodeError as exc:
        logger.warning("Invalid LANGUAGE_SERVERS JSON: %s", exc)
        return {}


def get_server_cmd(servers: dict[str, list[str]], ext: str) -> list[str] | None:
    """Return the server command for the given file extension, or None."""
    return servers.get(ext)
