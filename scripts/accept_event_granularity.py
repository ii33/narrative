"""One isolated generic source checks events rather than individual gestures."""
from __future__ import annotations

import json
from pathlib import Path

import accept_screenplay as qa

TEXT = """档案室里，林岚坐下，拿起文件，翻过两页，低头查看编号，又把文件放回桌上；她发现公开清单少了一页。
她起身取来备份，逐项核对，确认缺页涉及尚未获准公开的住址，于是决定暂停发布，并通知同事。
门外突然冒烟。林岚拉下警报开关，警报随即响起，楼内人员因此撤离；她留在出口引导最后两人离开。"""
TASK = '请读取指定短原文，按工程当前精细粒度提取场景、事件及所需的人物、地点和地点引用属性，通过统一工具自动入库。本次仅处理事件梳理，不提取物品、其他设定属性、动作节点或主要故事。原文编号已给出，直接读取此原文即可。必要时简洁说明语义存疑；提交账目由程序显示。'
TURN_QUOTES = ['发现公开清单少了一页', '决定暂停发布', '楼内人员因此撤离']


def seed(ops):
    ops.save_harness_settings({'provider': 'ark', 'model': 'ark-code-latest', 'reasoning_effort': 'low',
        'context_window': 1_000_000, 'output_reserve': 131_072, 'compaction_reserve': 65_536,
        'compaction_max_tokens': 32_768, 'max_tokens': 131_072})
    source = ops.import_source('档案室信息与决定短样本', TEXT)
    script = ops.create_screenplay('验收空容器', reason='统一验收器只读空容器')['screenplay']
    return {'source_id': source, 'screenplay_id': script['id'], 'source_before': ops.source(source)}


def assess_events(graph, source_id):
    events = [n for n in graph['nodes'] if n.get('node_type') == 'event']
    evidence = {n['id']: [s for s in n.get('evidence_spans', []) if s.get('source_id') == source_id] for n in events}
    exact = bool(events) and all(spans and all(TEXT[s['start']:s['end']] == s['quote'] for s in spans) for spans in evidence.values())
    coverage = []
    for quote in TURN_QUOTES:
        start = TEXT.index(quote)
        end = start + len(quote)
        coverage.append([eid for eid, spans in evidence.items() if any(s['start'] <= start and s['end'] >= end for s in spans)])
    distinct = any(len({a, b, c}) == 3 for a in coverage[0] for b in coverage[1] for c in coverage[2])
    return {'sample_event_count_not_gesture_chain': 3 <= len(events) <= 5,
        'independent_turns_have_distinct_events': distinct,
        'exact_event_evidence': exact,
        'no_default_actions': not any(n.get('node_type') == 'action' for n in graph['nodes'])}, {
        'events': events, 'turn_evidence_event_ids': coverage,
        'review_required': 'Read unchanged event names and evidence: one goal-directed inspection chain is not separate gesture events; an action causing an independent evacuation remains an event. Sample count bounds are acceptance-only, not product policy.'}


def validate(ops, fixture, screenplay, calls, report):
    graph = ops.graph_snapshot()
    checks, evidence = assess_events(graph, fixture['source_id'])
    audit = json.loads((Path(report['audit_directory']) / 'task.json').read_text(encoding='utf-8'))
    steps = audit['steps']
    accepted = [s for s in steps if s['tool'] in {'submit_extraction', 'patch_extraction_submission'} and s.get('ok') and s.get('result', {}).get('status') == 'accepted']
    static = ops.check_project()
    report['granularity_evidence'] = evidence | {'graph': graph, 'property_records': ops.property_records(), 'static_check': static}
    return checks | {'source_read': any(s['tool'] == 'read_source' and s.get('ok') for s in steps),
        'accepted_extraction': bool(accepted), 'static_no_errors': static['counts'].get('error', 0) == 0,
        'source_unchanged': ops.source(fixture['source_id']) == fixture['source_before']}


def main():
    qa.seed, qa.TEXT, qa.TASK = seed, TEXT, TASK
    qa.PROMPT_VERSION = 'event-granularity-real/2'
    qa.FIXTURE_ORIGIN = 'New isolated generic source. Every extraction is model-authored; no seeded events, answer hints, warmup, manual fixes or retries.'
    return qa.main(validate=validate, required_tools=['read_source', 'submit_extraction'],
        prompt_factory=lambda f: TASK + '\nsource_id=' + f['source_id'], use_writing_target=False, allow_extraction_change=True)


if __name__ == '__main__':
    raise SystemExit(main())
