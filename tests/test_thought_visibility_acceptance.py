"""Offline checks for the read-only public-thought acceptance validator."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def acceptance(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('thought_visibility_acceptance', scripts / 'accept_thought_visibility.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def messages():
    return [{'message_id': 'process', 'text': '我先查看工程概况。', 'display_kind': 'thought',
        'content': [{'type': 'text', 'text': '我先查看工程概况。'}, {'type': 'tool_use', 'name': 'project_info'}], 'full_output': 'original process'},
        {'message_id': 'final', 'text': '当前人物数量为0。', 'display_kind': 'answer',
        'content': [{'type': 'text', 'text': '当前人物数量为0。'}], 'full_output': 'original final'}]


def test_public_process_and_visible_answer_require_exact_metadata(acceptance):
    rows = messages()
    assert all(acceptance.assess_messages(rows, rows).values())
    metadata = [r.copy() for r in rows]
    metadata[0]['text'] = 'host synthetic progress'
    assert not acceptance.assess_messages(rows, metadata)['metadata_and_chat_text_match']


def test_tool_without_public_text_does_not_pass(acceptance):
    rows = messages()
    rows[0]['text'] = ''
    assert not acceptance.assess_messages(rows, rows)['public_tool_process_saved']


def test_private_reasoning_is_not_visible_progress(acceptance):
    rows = messages()
    rows[0]['content'].append({'type': 'thinking', 'thinking': 'private'})
    assert not acceptance.assess_messages(rows, rows)['no_private_blocks_in_visible_records']


def test_folded_final_answer_and_missing_raw_are_detected(acceptance):
    rows = messages()
    rows[-1]['display_kind'] = 'thought'
    rows[0]['full_output'] = None
    checks = acceptance.assess_messages(rows, rows)
    assert not checks['visible_final_answer_saved']
    assert not checks['public_raw_preserved']
