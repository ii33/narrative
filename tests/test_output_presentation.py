from narrative.output_presentation import OUTPUT_CONTRACT, present_message


def test_process_metadata_does_not_modify_raw_or_use_text_heuristics():
    event = {"text": "新增99项只是用户引用的文本", "full_output": "raw-original", "content": [{"type": "text", "text": "说明"}]}
    assert present_message(event)["display_kind"] == "answer"
    process = present_message(event | {"content": event["content"] + [{"type": "tool_use", "name": "save_asset"}]})
    assert process["display_kind"] == "thought" and process["full_output"] == "raw-original"
    assert present_message(event, "thought")["display_kind"] == "answer"
    assert present_message(event | {"display_kind": "thought"}, "thought")["display_kind"] == "thought"
    assert event.get("display_kind") is None


def test_contract_preserves_creative_answers_and_requires_no_private_reasoning():
    assert "不要展示私密详细推理" in OUTPUT_CONTRACT
    assert "创作正文" in OUTPUT_CONTRACT and "不另写终场总结" in OUTPUT_CONTRACT


def test_public_progress_is_required_without_requesting_private_reasoning_or_extra_tools():
    from narrative.prompt_defaults import DEFAULT_PROMPTS
    system = DEFAULT_PROMPTS['system']['text']
    extraction = DEFAULT_PROMPTS['extraction']['text']
    for text in (system, OUTPUT_CONTRACT):
        assert '公开过程说明' in text
        assert '实质进展、关键判断、问题或' in text
        assert '正常公开text' in text
        assert '只有工具输出' in text
        assert '私密详细推理' in text
    assert '不必每次工具调用都说明' in OUTPUT_CONTRACT
    assert '不为凑说明再调用工具' in OUTPUT_CONTRACT
    assert '不输出思考过程' not in extraction
    assert '不能将省略入库账目理解为省略公开说明' in extraction


def test_harness_keeps_public_text_beside_tools_and_filters_only_private_blocks(monkeypatch):
    from types import SimpleNamespace

    import deepseek_harness

    from narrative.harness import HarnessRunner
    events = [
        {'type': 'assistant/message', 'seq': 1, 'data': {'content': [
            {'type': 'thinking', 'thinking': 'PRIVATE_SYNTHETIC_PLACEHOLDER'},
            {'type': 'text', 'text': '先核对原文依据。'},
            {'type': 'tool-call', 'id': 'call-1', 'name': 'read_source', 'arguments': {}}]}},
        {'type': 'assistant/message', 'seq': 2, 'data': {'content': [
            {'type': 'reasoning', 'text': 'PRIVATE_SYNTHETIC_PLACEHOLDER'}]}},
        {'type': 'assistant/message', 'seq': 3, 'data': {'content': [
            {'type': 'redacted_thinking', 'data': 'PRIVATE_SYNTHETIC_PLACEHOLDER'},
            {'type': 'text', 'text': '这是用户需要的最终解释。'}]}},
    ]
    class Session:
        def __init__(self, _harness, session_id):
            self.session_id = session_id
        def run(self, _instruction, on_notification):
            for event in events:
                on_notification(SimpleNamespace(method='session.event', payload={'sessionId': self.session_id, 'event': event}))
            return SimpleNamespace(events=events, final_response='这是用户需要的最终解释。', session_id=self.session_id, finish_reason='stop')
    monkeypatch.setattr(deepseek_harness, 'Session', Session)
    runner = object.__new__(HarnessRunner)
    runner._closed = False
    runner._secret = ''
    runner._steering_listener = None
    runner._usage_listener = None
    runner.settings = SimpleNamespace(provider='local-offline')
    runner._harness = SimpleNamespace(start=lambda: None)
    runner._gateway = SimpleNamespace(error=None, token='test-tool-token', usage_snapshot=lambda: [],
        compaction_failure_snapshot=lambda: {'compaction_incomplete': False})
    monkeypatch.setattr(runner, 'usage_metadata', lambda: {})
    observed = []
    reply = runner._run('本地事件回放，不请求服务', 'session-local', on_message=observed.append)
    assert [row['seq'] for row in observed] == [1, 3]
    assert observed[0]['text'] == '先核对原文依据。'
    assert observed[0]['display_kind'] == 'thought'
    assert observed[1]['display_kind'] == 'answer'
    assert reply.metadata['model_messages'] == observed
    assert 'PRIVATE_SYNTHETIC_PLACEHOLDER' not in str(observed)
    assert reply.raw == '这是用户需要的最终解释。'


def test_writing_preference_does_not_fold_plain_final_creative_results_or_override_explicit_progress():
    final = {'text': '甲把包递给乙。\n甲：明天再见。', 'content': [{'type': 'text', 'text': '甲把包递给乙。\n甲：明天再见。'}], 'full_output': 'unchanged-final'}
    assert present_message(final, 'thought')['display_kind'] == 'answer'
    progress = final | {'text': '已核对人物与语境，接着保存。', 'display_kind': 'thought'}
    assert present_message(progress, 'answer')['display_kind'] == 'thought'
    # No keyword test determines whether quoted "思考" belongs in an answer.
    quoted = final | {'text': '角色说：“我需要思考。”'}
    assert present_message(quoted, 'thought')['display_kind'] == 'answer'
