from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"


@pytest.fixture(scope="session")
def raw_items() -> list[dict]:
    return json.loads((FIXTURES / "items.json").read_text(encoding="utf-8"))["items"]


@pytest.fixture(scope="session")
def raw_sets() -> list[dict]:
    return json.loads((FIXTURES / "sets.json").read_text(encoding="utf-8"))["sets"]


@pytest.fixture(scope="session")
def full_catalog() -> tuple[list[dict], list[dict]]:
    """Catalogue complet issu du cache d'ingestion.

    Absent d'un clone neuf : les tests qui en dépendent sont ignorés tant que
    `python -m dofus_opti.ingest.build` n'a pas tourné.
    """
    items_path = CACHE / "equipment_all.json"
    sets_path = CACHE / "sets_all.json"
    if not items_path.exists() or not sets_path.exists():
        pytest.skip("cache absent — lancez d'abord `python -m dofus_opti.ingest.build`")
    return (
        json.loads(items_path.read_text(encoding="utf-8"))["items"],
        json.loads(sets_path.read_text(encoding="utf-8"))["sets"],
    )
