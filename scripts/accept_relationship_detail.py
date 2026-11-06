"""One isolated extraction tests parallel relations, scope, direction and endings."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = '''林岚与许禾在灯塔工作室见面。两人既是朋友，也是同事。周宁是林岚的父亲，今天也来工作室探望她。
许禾指着来访的陈墨对林岚说：“陈墨是我的老师。”旁白说明：这只是许禾单方面的称呼；陈墨是来访的编辑，两人从未建立师生关系。
许禾走上工作室门外的平台，险些跌下去。门口的修理员及时扶住许禾，将她救回平台。
回到工作室，林岚与许禾因工作安排争执。两人都明确说，争执没有改变她们的朋友关系。
当天傍晚，双方正式结束共同工作，林岚说：“从这一刻起，我们不再是同事。”许禾确认此事。旁白说明，两人的同事关系到此终止。
次日，林岚与许禾以朋友身份再次在工作室见面。两人仍是朋友，但已不再是同事。'''
TASK = '请读取指定短原文，按当前精细粒度完整提取人物、地点、事件、属性及关系，核对关系性质、对象、方向、并存、事实与单方主张、建立和终止证据；通过统一工具自动入库。无需动作或主要故事；只处理这份原文。提交账目由程序显示，仅补充必要的语义说明与存疑。'
EXPECTED = [
    {'meaning': '朋友与同事同时成立', 'entities': ['林岚', '许禾'], 'scope': 'world', 'evidence': '两人既是朋友，也是同事。'},
    {'meaning': '周宁是林岚父亲，不能把方向倒置', 'entities': ['林岚', '周宁'], 'scope': 'world', 'evidence': '周宁是林岚的父亲'},
    {'meaning': '老师只是许禾主张，不能成为客观师生关系', 'entities': ['许禾', '陈墨'], 'scope': 'claim or cognition with holder 许禾', 'evidence': '这只是许禾单方面的称呼'},
    {'meaning': '次要无名修理员对许禾实际施救，不外推长期朋友或恩师关系', 'entities': ['门口的修理员', '许禾'], 'scope': 'world, anchored occurrence', 'evidence': '门口的修理员及时扶住许禾，将她救回平台。'},
    {'meaning': '同事关系保留历史并在明确终止处结束，朋友关系后续存续', 'entities': ['林岚', '许禾'], 'scope': 'world', 'evidence': '两人仍是朋友，但已不再是同事。'},
]


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source = ops.import_source('并存关系与终止短样本', TEXT)
    script = ops.create_screenplay('验收空容器', reason='统一验收器只读空容器')['screenplay']
    return {'source_id': source, 'screenplay_id': script['id'], 'source_before': ops.source(source), 'semantic_expectations': EXPECTED}


def relation_evidence(graph, records):
    names = {n['id']: n['name'] for n in graph['nodes']}
    definitions = {r['id']: r for r in records if r.get('kind') == 'property_definition'}
    rows = []
    for row in records:
        if row.get('kind') != 'property_change':
            continue
        values = row.get('value') if isinstance(row.get('value'), list) else [row.get('value')]
        for value in values:
            if not isinstance(value, str) or value not in names:
                continue
            rows.append({'record': row, 'subject': names.get(row.get('node_id')), 'object': names[value],
                'holder': names.get(row.get('holder_id')), 'definition': definitions.get(row.get('property_id'))})
    return rows


def validate(ops, fixture, screenplay, calls, report):
    graph, records = ops.graph_snapshot(), ops.property_records()
    from narrative.temporal_state import projected_changes
    rows = relation_evidence(graph, records)
    current_rows = relation_evidence(graph, projected_changes(graph, records))
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    accepted = [s for s in audit['steps'] if s['tool'] == 'submit_extraction' and s.get('ok') and s.get('result', {}).get('status') == 'accepted']
    pair = [r for r in rows if {r['subject'], r['object']} == {'林岚', '许禾'} and r['record'].get('scope', 'world') == 'world']
    attributed = [r for r in rows if {r['subject'], r['object']} == {'许禾', '陈墨'} and r['record'].get('scope') in {'claim', 'cognition'} and r['holder'] == '许禾']
    names = {n['name'] for n in graph['nodes']}
    spans = [s for n in graph['nodes'] for s in n.get('evidence_spans', [])]
    check = ops.check_project()
    report['relation_evidence'] = {'graph': graph, 'property_records': records, 'reference_relations': rows, 'current_reference_relations': current_rows,
        'parallel_pair_records': pair, 'attributed_pair_records': attributed, 'semantic_expectations': EXPECTED,
        'semantic_review': 'Actual property names are intentionally unconstrained. Review every expected meaning against these unmodified records and quotes; structural checks alone do not certify relation semantics or lasting validity of a one-time act.',
        'accepted_submissions': accepted, 'static_check': check}
    return {'source_read': any(s['tool'] == 'read_source' and s.get('ok') for s in audit['steps']),
        'accepted_extraction': bool(accepted) and all(s['result'].get('valid') for s in accepted),
        'named_people_covered': {'林岚', '许禾', '周宁', '陈墨'} <= names,
        'minor_person_covered': any(n['node_type'] == 'person' and '修理员' in n['name'] for n in graph['nodes']),
        'parallel_pair_distinct_properties': len({r['record']['property_id'] for r in pair}) >= 2,
        'pair_has_removal_and_continuing_state': any(r['record'].get('op') == 'remove' for r in pair) and any({r['subject'], r['object']} == {'林岚', '许禾'} and r['record'].get('scope', 'world') == 'world' for r in current_rows),
        'event_changes_without_validity': all(r.get('kind') != 'property_value' and not {'valid_from', 'valid_to', 'applies_to'} & set(r) for r in records),
        'single_party_attribution_present': bool(attributed),
        'exact_program_coordinates': bool(spans) and all(s['source_id'] == fixture['source_id'] and TEXT[s['start']:s['end']] == s['quote'] for s in spans),
        'no_default_actions': not any(n['node_type'] == 'action' for n in graph['nodes']),
        'static_no_errors': check['counts'].get('error', 0) == 0,
        'source_unchanged': ops.source(fixture['source_id']) == fixture['source_before']}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = 'relationship-event-changes-real/2'
    qa.FIXTURE_ORIGIN = 'New isolated generic short source; model authors every extraction. No seeded relations, manual corrections, warmup or service retries.'
    return qa.main(validate=validate, required_tools=['read_source', 'submit_extraction'], prompt_factory=lambda f: TASK+'\nsource_id='+f['source_id'], use_writing_target=False, allow_extraction_change=True)


if __name__ == '__main__':
    raise SystemExit(main())
