"""Malformed interval shapes are rejected, never repaired or persisted."""
from __future__ import annotations

import copy

import pytest
from agent_fixtures import project as project
from test_current_state_contract import QUOTES, TEXT, setup


@pytest.mark.parametrize(('field', 'value'), [
    ('valid_from', []), ('valid_from', 'later'), ('valid_from', None),
    ('valid_to', []), ('valid_to', 'later'),
    ('valid_from', {'source_id': 'missing', 'start': 0}),
])
def test_invalid_interval_shape_rejected_without_state_change(project, field, value):
    sid, ids = setup(project)
    row = {'kind': 'property_value', 'id': 'bad-boundary', 'node_id': ids['甲'],
           'property_id': '称呼', 'value': 'bad', 'event_id': ids[QUOTES[0][:-1]],
           'disclosure': {'source_id': sid, 'start': TEXT.index(QUOTES[0])},
           'evidence': {'source_id': sid, 'quote': QUOTES[0]}, field: value}
    before = copy.deepcopy(project.graph_snapshot())
    records = project.property_records()
    result = project.preview_changes([], [row], before['revision'])
    assert result['valid'] is False
    assert any(issue['severity'] == 'error' for issue in result['diagnostics'])
    assert project.graph_snapshot() == before
    assert project.property_records() == records


def test_unrecognized_boundary_field_is_not_silently_stripped(project):
    sid, ids = setup(project)
    row = {'kind': 'property_value', 'id': 'extra-boundary', 'node_id': ids['甲'],
           'property_id': '称呼', 'value': 'A', 'event_id': ids[QUOTES[0][:-1]],
           'valid_from': {'source_id': sid, 'start': TEXT.index(QUOTES[0]), 'unexpected': True},
           'evidence': {'source_id': sid, 'quote': QUOTES[0]}}
    result = project.preview_changes([], [row], project.graph_project_info()['revision'])
    assert not result['valid']
    assert any(issue['code'] == 'VALIDITY' for issue in result['diagnostics'])
