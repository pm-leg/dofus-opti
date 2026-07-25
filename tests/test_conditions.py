from __future__ import annotations

from dofus_opti.ingest.conditions import (
    condition_from_dict,
    condition_to_dict,
    condition_to_text,
    parse_condition,
)
from dofus_opti.ingest.normalize import IngestReport, normalize_items
from dofus_opti.model.items import ConditionNode, ConditionOp, ConditionSubject, LeafCondition
from dofus_opti.model.stats import StatKey


def _leaf(element_id, name, operator, value):
    return {
        "condition": {
            "operator": operator,
            "int_value": value,
            "element": {"id": element_id, "name": name},
        },
        "is_operand": True,
    }


def test_simple_stat_condition():
    unknown: dict[int, str] = {}
    cond = parse_condition(_leaf(45, "Force", ">", 500), unknown)

    assert isinstance(cond, LeafCondition)
    assert cond.subject is ConditionSubject.STAT
    assert cond.stat is StatKey.FORCE
    assert cond.operator is ConditionOp.GT
    assert cond.value == 500
    assert not unknown


def test_level_condition_is_not_a_stat():
    cond = parse_condition(_leaf(200, "Être niveau {0} ou plus", ">", 174), {})
    assert isinstance(cond, LeafCondition)
    assert cond.subject is ConditionSubject.LEVEL
    assert cond.stat is None


def test_nested_tree_is_flattened_correctly(raw_items):
    """« La Baguette des Limbes » : (PA < 12 ET PM < 6) ET Sagesse > 99."""
    report = IngestReport()
    items = {i.name: i for i in normalize_items(raw_items, report)}
    cond = items["La Baguette des Limbes"].condition

    assert isinstance(cond, ConditionNode)
    assert cond.relation == "and"

    leaves: list[LeafCondition] = []

    def collect(node):
        if isinstance(node, ConditionNode):
            for child in node.children:
                collect(child)
        else:
            leaves.append(node)

    collect(cond)
    assert {(l.stat, l.operator.value, l.value) for l in leaves} == {
        (StatKey.PA, "<", 12),
        (StatKey.PM, "<", 6),
        (StatKey.SAGESSE, ">", 99),
    }


def test_single_child_node_is_collapsed():
    raw = {"relation": "and", "children": [_leaf(45, "Force", ">", 100)], "is_operand": False}
    cond = parse_condition(raw, {})
    assert isinstance(cond, LeafCondition)


def test_unknown_element_is_collected_not_raised():
    unknown: dict[int, str] = {}
    cond = parse_condition(_leaf(99999, "Nouvelle Condition", ">", 1), unknown)
    assert cond is None
    assert unknown == {99999: "Nouvelle Condition"}


def test_serialization_roundtrip(raw_items):
    report = IngestReport()
    items = normalize_items(raw_items, report)
    for item in items:
        if item.condition is None:
            continue
        assert condition_from_dict(condition_to_dict(item.condition)) == item.condition


def test_text_rendering():
    cond = ConditionNode(
        relation="and",
        children=(
            LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 500, StatKey.FORCE),
            LeafCondition(ConditionSubject.STAT, ConditionOp.LT, 12, StatKey.PA),
        ),
    )
    assert condition_to_text(cond) == "(force > 500 ET pa < 12)"
