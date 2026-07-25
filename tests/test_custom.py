"""Items du joueur et pool de Dofus déclaré."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dofus_opti.model.stats import StatKey
from dofus_opti.optim.custom import CustomItemError, CustomItemSpec, apply_custom_items
from dofus_opti.optim.pool import build_pool
from dofus_opti.optim.request import BuildRequest

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"


# ----------------------------------------------------------------- analyse

def test_parses_a_delta_override():
    spec = CustomItemSpec.parse("Gelano:pm=+1")
    assert spec.base_name == "Gelano"
    assert spec.overrides == {StatKey.PM: ("delta", 1)}


def test_parses_several_overrides_and_both_modes():
    spec = CustomItemSpec.parse("Cape Vent:pa=+1,force=80,pm=-1")
    assert spec.overrides == {
        StatKey.PA: ("delta", 1),
        StatKey.FORCE: ("set", 80),
        StatKey.PM: ("delta", -1),
    }


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("Gelano", "format attendu"),
        (":pm=+1", "nom d'item manquant"),
        ("Gelano:pm", "attendu « stat=valeur »"),
        ("Gelano:bidule=+1", "caractéristique inconnue"),
        ("Gelano:pm=beaucoup", "n'est pas un entier"),
        ("Gelano:", "aucune modification"),
    ],
)
def test_rejects_malformed_specs(text, fragment):
    with pytest.raises(CustomItemError) as exc:
        CustomItemSpec.parse(text)
    assert fragment in str(exc.value)


def test_description_is_readable():
    assert "pm +1" in CustomItemSpec.parse("Gelano:pm=+1").describe()


# ------------------------------------------------------------ construction

@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    yield connection
    connection.close()


def test_a_forged_gelano_keeps_its_base_stats(conn):
    from dofus_opti.optim.pool import load_items

    items = load_items(conn, 200)
    built, replaced, _ = apply_custom_items(
        conn, [CustomItemSpec.parse("Gelano:pm=+1")], items
    )
    gelanos = [i for i in built if "Gelano" in i.name and i.level == 60]

    assert len(gelanos) == 1, "le modèle du catalogue doit être remplacé"
    forged = gelanos[0]
    assert forged.name == "Gelano (perso)"
    assert forged.stat(StatKey.PA) == 1, "le +1 PA d'origine est conservé"
    assert forged.stat(StatKey.PM) == 1, "l'exo PM est ajouté"
    assert replaced, "l'identifiant du modèle est signalé comme remplacé"


def test_a_set_override_replaces_rather_than_adds(conn):
    from dofus_opti.optim.pool import load_items

    items = load_items(conn, 200)
    built, _, _ = apply_custom_items(conn, [CustomItemSpec.parse("Gelano:pa=3")], items)
    forged = next(i for i in built if i.name == "Gelano (perso)")
    assert forged.stat(StatKey.PA) == 3


def test_an_unknown_base_item_is_reported_with_suggestions(conn):
    from dofus_opti.optim.pool import load_items

    with pytest.raises(CustomItemError) as exc:
        apply_custom_items(
            conn, [CustomItemSpec.parse("Gelanoo:pm=+1")], load_items(conn, 200)
        )
    assert "introuvable" in str(exc.value)


# --------------------------------------------------------- pool de Dofus

def test_declaring_dofus_leaves_trophies_available(conn):
    """261 trophées et 25 prysmaradites partagent l'emplacement : ils restent."""
    ids = {}
    for name in ("Dofus Ocre", "Dofus Pourpre", "Dofus Émeraude"):
        row = conn.execute("SELECT ankama_id FROM item WHERE name = ?", (name,)).fetchone()
        ids[name] = row[0]

    request = BuildRequest(
        level=175, breed="Iop", elements={"terre"}, allowed_dofus=set(ids.values())
    )
    # Des poids larges pour que l'élagage ne masque pas ce qu'on mesure.
    weights = {key: 1.0 for key in StatKey}
    items, _, _ = build_pool(conn, request, weights)

    dofus = {i.name for i in items if i.type_name == "Dofus"}
    trophies = [i for i in items if i.type_name in ("Trophée", "Prysmaradite")]

    assert dofus <= set(ids), f"Dofus non déclarés dans le pool : {dofus - set(ids)}"
    assert dofus, "les Dofus déclarés doivent rester"
    assert len(trophies) > 50, "les trophées ne doivent pas être restreints"


def test_without_declaration_every_dofus_is_allowed(conn):
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    weights = {key: 1.0 for key in StatKey}
    items, _, _ = build_pool(conn, request, weights)

    dofus = [i for i in items if i.type_name == "Dofus"]
    assert len(dofus) > 10


def test_custom_items_escape_the_obtainability_filter(conn):
    """Un item que le joueur possède déjà n'a pas à justifier sa provenance."""
    request = BuildRequest(
        level=175, breed="Iop", elements={"terre"},
        custom_specs=[CustomItemSpec.parse("Gelano:pm=+1")],
        require_obtainable=True,
    )
    weights = {StatKey.PA: 100.0, StatKey.FORCE: 1.0}
    items, _, report = build_pool(conn, request, weights)

    assert any(i.name == "Gelano (perso)" for i in items)
    assert report.custom_added == 1
