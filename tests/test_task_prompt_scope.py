"""Task envelopes do not preload unrelated editable workflows."""
from narrative.assistant import _first_prompt


def test_simple_query_carries_only_task_context_not_unrelated_guides():
    config = {'texts': {key: 'UNRELATED_' + key for key in ['extraction', 'review', 'event_merge_review', 'granularity_fine']}}
    prompt = _first_prompt('查询剧本列表', prompt_config=config, project_style={'tone': '温和'})
    assert '用户说：查询剧本列表' in prompt
    assert '当前工程提取粒度：fine' in prompt and '温和' in prompt
    assert 'UNRELATED_' not in prompt
    assert 'submit_extraction' not in prompt and 'merge_events' not in prompt


def test_attachment_note_and_current_granularity_remain_in_lean_envelope():
    prompt = _first_prompt('查看附图', image='attached.png', extraction_granularity='standard')
    assert '当前工程提取粒度：standard' in prompt
    assert '用户附图已随本条输入提供' in prompt
