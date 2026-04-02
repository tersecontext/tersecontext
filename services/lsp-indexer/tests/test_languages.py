import json
import pytest
from app.languages import detect_languages, parse_language_servers, get_server_cmd


def test_detect_languages_py():
    files = ["src/auth.py", "src/models.py", "README.md"]
    assert detect_languages(files) == {"py"}


def test_detect_languages_multi():
    files = ["main.go", "handler.go", "app.py"]
    result = detect_languages(files)
    assert result == {"go", "py"}


def test_detect_languages_skips_unknown():
    files = ["README.md", "Makefile", "image.png"]
    assert detect_languages(files) == set()


def test_parse_language_servers_valid():
    cfg = json.dumps({"py": ["pyright-langserver", "--stdio"], "go": ["gopls"]})
    servers = parse_language_servers(cfg)
    assert servers["py"] == ["pyright-langserver", "--stdio"]
    assert servers["go"] == ["gopls"]


def test_parse_language_servers_empty_string():
    assert parse_language_servers("") == {}


def test_parse_language_servers_invalid_json():
    assert parse_language_servers("{bad json}") == {}


def test_get_server_cmd_known():
    servers = {"py": ["pyright-langserver", "--stdio"]}
    assert get_server_cmd(servers, "py") == ["pyright-langserver", "--stdio"]


def test_get_server_cmd_unknown():
    assert get_server_cmd({}, "rs") is None
