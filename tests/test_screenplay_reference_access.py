"""Broad reference reading does not silently broaden creation or lose concurrency."""
from __future__ import annotations

import json

import pytest
from test_screenplay_input_boundaries import context as context
from test_screenplay_writing import project as project
from test_screenplay_writing import table

from narrative.screenplay_store import read_context


def test_explicit_suppressed_reference_is_readable_without_expanding_default_creation(context):
    ops, segment_id, ids = context
    default = ops.read_scene_table_context(segment_id)
    refs = [{'kind': 'adaptation_entity', 'id': ids['焚图']}]
    explicit = ops.read_scene_table_context(segment_id, dependency_refs=refs)
    reference = next(row['input'] for row in explicit['reference_context']['dependencies'] if row['kind'] == 'adaptation_entity')
    assert reference['name'] == '焚图'
    assert reference['reference_only'] and reference['current_creation_excluded']
    assert explicit['span']['effective_node_ids'] == default['span']['effective_node_ids']
    assert ids['焚图'] not in explicit['span']['effective_node_ids']
    assert '焚图' not in json.dumps(default['span']['input'], ensure_ascii=False)
    assert '屏蔽' in explicit['reference_policy']['guidance']
    script_id = ops.read_script_segment(segment_id)['screenplay_id']
    local = ops.read_screenplay_timeline(script_id)
    ops.edit_screenplay_timeline_node(script_id, ids['焚图'], {'name': '参考已由用户修改'},
        expected_timeline_revision=local['revision'], reason='更正显式参考')
    assert ops.read_scene_table_context(segment_id)['context_fingerprint'] == default['context_fingerprint']
    assert ops.read_scene_table_context(segment_id, dependency_refs=refs)['context_fingerprint'] != explicit['context_fingerprint']
    before = read_context(ops)[0]
    with pytest.raises(ValueError, match='上下文'):
        ops.submit_scene_table(segment_id, [{'plan': {'summary': '甲继续等待。'}}], explicit['expected_outline_revision'],
            explicit['span_fingerprint'], explicit['context_fingerprint'], dependency_refs=refs, reason='拒绝旧参考结果')
    assert read_context(ops)[0] == before


def test_explicit_previous_from_another_screenplay_reads_its_owner_and_checks_staleness(project):
    ops, screenplay_id, segment_id = project
    table(ops, segment_id)
    source = ops.adaptation_snapshot()
    other = ops.create_screenplay('供参考的另一剧本', reason='跨剧本参考')['screenplay']
    local = ops.import_screenplay_timeline(other['id'], [n['id'] for n in source['nodes'] if n['node_type'] == 'big_event'],
        0, source['revision'], '独立参考副本')
    events = [n['id'] for n in local['nodes'] if n['node_type'] == 'event']
    span = ops.create_adaptation_span(other['id'], '参考段范围', start_node_id=events[0], end_node_id=events[-1],
        expected_timeline_revision=local['revision'], reason='参考范围')['span']
    target = ops.create_script_segment(other['id'], span['id'], '另一剧本段', other['structure_revision'], '建立参考段')
    units = table(ops, target['id'])['units']
    previous, current = units
    text = '另一剧本前场正文可供参考。'
    ops.apply_script_block_changes(previous['id'], [{'op': 'insert', 'block': {'type': 'action', 'payload': {'text': text}}}],
        [], previous['body_revision'], reason='参考正文')
    refs = [{'kind': 'previous_unit', 'id': current['id']}, {'kind': 'unit_plan', 'id': current['id']}, {'kind': 'unit_body', 'id': previous['id']}]
    context = ops.read_scene_table_context(segment_id, dependency_refs=refs)
    references = context['reference_context']['dependencies']
    preceding = next(row for row in references if row['kind'] == 'previous_unit')
    assert preceding['source_screenplay_id'] == other['id']
    assert preceding['input']['previous_unit_id'] == previous['id']
    assert text in json.dumps(preceding['input'], ensure_ascii=False)
    before_target = ops.read_script_segment(segment_id)
    previous = ops.read_script_unit_context(previous['id'])['unit']
    block = previous['blocks'][0]
    ops.apply_script_block_changes(previous['id'], [{'op': 'update', 'block_id': block['id'],
        'block': {'type': 'action', 'payload': {'text': '参考前场刚被用户更正。'}}}], [block['id']], previous['body_revision'], reason='并发更正参考')
    refreshed = ops.read_scene_table_context(segment_id, dependency_refs=refs)
    assert refreshed['context_fingerprint'] != context['context_fingerprint']
    assert ops.read_script_segment(segment_id) == before_target


def test_general_prompt_declares_user_workflow_and_reading_separate_from_writing():
    from narrative.assistant import ASSISTANT_VERSION
    from narrative.harness import CONVERSATION_SYSTEM_PROMPT
    from narrative.native_bridge import (
        SCREENPLAY_SCENE_TABLE_RULES,
        SCREENPLAY_WRITING_PROMPT_VERSION,
    )

    assert ASSISTANT_VERSION == 'conversation-agent/35'
    assert SCREENPLAY_WRITING_PROMPT_VERSION == 'screenplay-writing-ui/9'
    for phrase in ('固定创作阶段', '全部原文', '考据', '参考', '设定', '备注', '只修改用户明确指定'):
        assert phrase in CONVERSATION_SYSTEM_PROMPT
    assert '可查阅参考' in SCREENPLAY_SCENE_TABLE_RULES
    assert '只通过read_scene_table_context' not in SCREENPLAY_SCENE_TABLE_RULES
