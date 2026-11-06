"""Acceptance relation projection keeps scope, direction and event-change evidence."""
import importlib.util
import sys
from pathlib import Path


def test_relation_projection_does_not_require_fixed_property_names(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('relationship_acceptance', scripts / 'accept_relationship_detail.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    graph = {'nodes': [{'id': 'person-a', 'name': '甲'}, {'id': 'person-b', 'name': '乙'}]}
    original = {'kind': 'property_change', 'node_id': 'person-a', 'property_id': '自定义关系性质', 'value': 'person-b', 'scope': 'claim', 'holder_id': 'person-a', 'op': 'remove', 'event_id': 'event-end', 'sequence': 8}
    records = [original, {'kind': 'property_change', 'node_id': 'person-a', 'value': ['not', 'a', 'node']}]
    rows = module.relation_evidence(graph, records)
    assert len(rows) == 1
    assert rows[0]['record'] is original
    assert rows[0]['subject'] == '甲' and rows[0]['object'] == '乙' and rows[0]['holder'] == '甲'
    assert rows[0]['record']['op'] == 'remove' and rows[0]['record']['event_id'] == 'event-end'

    collection = original | {'value': ['person-b', 'person-a']}
    projected = module.relation_evidence(graph, [collection])
    assert len(projected) == 2 and {row['object'] for row in projected} == {'甲', '乙'}
    assert all(row['record'] is collection for row in projected)
