# services/instrumenter/tests/test_config.py
from app.config import PATCH_CATALOG
from app.models import PatchSpec

KNOWN_TARGETS = {
    "sqlalchemy.orm.Session.execute",
    "sqlalchemy.engine.Engine.connect",
    "psycopg2.connect",
    "httpx.Client.request",
    "httpx.AsyncClient.request",
    "requests.request",
    "builtins.open",
}


def test_known_targets_present():
    catalog_targets = {entry["target"] for entry in PATCH_CATALOG}
    assert KNOWN_TARGETS.issubset(catalog_targets)


def test_all_actions_valid():
    for entry in PATCH_CATALOG:
        PatchSpec(**entry)  # raises ValidationError if invalid action


def test_catalog_validates_on_import():
    from app.config import _validated
    assert len(_validated) == len(PATCH_CATALOG)
