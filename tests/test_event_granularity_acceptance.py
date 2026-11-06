"""Offline acceptance-validator checks; these do not certify model output."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def acceptance(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('event_granularity_acceptance', scripts / 'accept_event_granularity.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(module, number, quote):
    start = module.TEXT.index(quote)
    return {'id': f'event-{number}', 'node_type': 'event', 'name': quote,
        'evidence_spans': [{'source_id': 'source-sample', 'start': start, 'end': start + len(quote), 'quote': quote}]}


def test_independent_information_decision_and_action_consequence_pass(acceptance):
    graph = {'nodes': [event(acceptance, i, q) for i, q in enumerate(acceptance.TURN_QUOTES)]}
    checks, _ = acceptance.assess_events(graph, 'source-sample')
    assert all(checks.values())
    assert len(acceptance.TEXT) <= 180
    assert all(q not in acceptance.TASK for q in acceptance.TURN_QUOTES)


def test_whole_source_single_event_does_not_hide_missing_turns(acceptance):
    checks, _ = acceptance.assess_events({'nodes': [event(acceptance, 1, acceptance.TEXT)]}, 'source-sample')
    assert not checks['independent_turns_have_distinct_events']
    assert not checks['sample_event_count_not_gesture_chain']


def test_gesture_events_and_default_actions_fail(acceptance):
    nodes = [event(acceptance, i, q) for i, q in enumerate(acceptance.TURN_QUOTES)]
    nodes += [event(acceptance, i + 3, q) for i, q in enumerate(['坐下', '拿起文件', '翻过两页'])]
    nodes.append({'id': 'action-unrequested', 'node_type': 'action'})
    checks, _ = acceptance.assess_events({'nodes': nodes}, 'source-sample')
    assert not checks['sample_event_count_not_gesture_chain']
    assert not checks['no_default_actions']


def test_forged_coordinates_fail_even_with_correct_names(acceptance):
    nodes = [event(acceptance, i, q) for i, q in enumerate(acceptance.TURN_QUOTES)]
    nodes[0]['evidence_spans'][0]['quote'] = 'not the source'
    checks, _ = acceptance.assess_events({'nodes': nodes}, 'source-sample')
    assert not checks['exact_event_evidence']
