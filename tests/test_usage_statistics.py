"""Consumption accounting and context samples never infer hidden tokens."""
import json
from pathlib import Path

import pytest

from narrative import usage_statistics as usage
from narrative.chat_store import ChatStore
from narrative.operations import Operations
from narrative.project_package import layout


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'usage.nproj')
    chat = ChatStore(ops.project_info().path).create('统计测试')
    return ops, chat['id']


def request(input=100, output=20, **fields):
    return {'purpose': 'generation', 'complete': True, 'usage': {'prompt_tokens': input, 'completion_tokens': output, 'total_tokens': input + output, 'prompt_tokens_details': {'cached_tokens': 40}}, **fields}


def audit(ops, chat_id, task_id, calls):
    path = layout(ops.project_info().path).extraction / 'assistant' / 'tasks' / task_id / 'task.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'task_id': task_id, 'chat_id': chat_id, 'calls': calls}), encoding='utf-8')
    return path


def test_openai_cache_is_subset_and_messages_cache_is_additional():
    openai = usage.normalize_usage({'prompt_tokens': 150, 'completion_tokens': 10, 'prompt_tokens_details': {'cached_tokens': 40, 'cache_write_tokens': 10}})
    messages = usage.normalize_usage({'input_tokens': 100, 'output_tokens': 10, 'cache_read_input_tokens': 40, 'cache_creation_input_tokens': 10})
    for normalized in (openai, messages):
        assert normalized['input_tokens'] == 150 and normalized['total_tokens'] == 160
        assert normalized['cache_read_tokens'] == 40 and normalized['cache_write_tokens'] == 10
    assert usage.normalize_usage({'input_tokens': 10, 'output_tokens': 2})['cache_write_tokens'] is None
    assert all(usage.normalize_usage(None)[key] is None for key in usage.METRICS)


def test_chat_and_task_totals_deduplicate_live_final_and_sdk_copy(ledger):
    ops, chat = ledger
    turn = {'usage_turn_id': 'sdk-turn-1', 'model_requests': [request()], 'usage': [{'prompt_tokens': 99999}]}
    path = audit(ops, chat, 'task1', [turn])
    original = path.read_bytes()
    usage.publish_live_usage(ops.project_info().path, chat, 'task1', turn, turn_id='sdk-turn-1')
    first = ops.read_usage_statistics(chat, 'task1')
    assert first['task_usage']['requests'] == 1 and first['chat_usage']['total_tokens'] == 120
    assert first['chat_usage']['input_tokens'] == 100
    usage.publish_live_usage(ops.project_info().path, chat, 'task1', turn, turn_id='sdk-turn-1')
    audit(ops, chat, 'task2', [{'usage_turn_id': 'sdk-turn-2', 'model_requests': [request(50, 5)]}])
    second = ops.read_usage_statistics(chat, 'task2')
    assert second['chat_usage']['requests'] == 2 and second['chat_usage']['total_tokens'] == 175
    assert second['task_usage']['total_tokens'] == 55
    assert path.read_bytes() == original


def test_unknown_request_keeps_partial_totals_separate(ledger):
    ops, chat = ledger
    audit(ops, chat, 'partial', [{'model_requests': [request(), {'purpose': 'generation', 'complete': False, 'usage': None}]}])
    result = ops.read_usage_statistics(chat, 'partial')['task_usage']
    assert result['requests'] == 2 and result['unknown_requests'] == 1
    assert result['total_tokens'] is None and result['known_totals']['total_tokens'] == 120
    assert result['cache_write_tokens'] is None and result['incomplete']


def test_new_task_keeps_chat_context_sample_without_adding_prior_task_usage(ledger):
    ops, chat = ledger
    audit(ops, chat, 'finished', [{'usage_turn_id': 'previous', 'model_requests': [request(400, 20)]}])
    ops.read_usage_statistics(chat)
    result = usage.publish_live_usage(ops.project_info().path, chat, 'starting', {
        'usage_turn_id': 'current', 'model_requests': [],
        'context_policy': {'context_window': 1000},
    })
    assert result['task_usage']['requests'] == 0
    assert result['chat_usage']['requests'] == 1
    assert result['context']['last_known_input_tokens'] == 400
    assert result['context']['input_tokens'] == 400 and result['context']['stale']
    assert result['context']['context_window'] == 1000


def test_context_is_last_generation_not_cumulative_and_compaction_expires_it(ledger):
    ops, chat = ledger
    policy = {'context_window': 1000, 'compaction_threshold_tokens': 800, 'effective_output_reserve': 100}
    metadata = {'usage_turn_id': 'turn-context', 'context_policy': policy, 'model_requests': [request(300, 20), request(400, 30)]}
    usage.publish_live_usage(ops.project_info().path, chat, 'task', metadata)
    result = ops.read_usage_statistics(chat, 'task')
    assert result['chat_usage']['input_tokens'] == 700 and result['context']['input_tokens'] == 400
    metadata['model_requests'].append(request(500, 50, purpose='compaction'))
    usage.publish_live_usage(ops.project_info().path, chat, 'task', metadata)
    compacting = ops.read_usage_statistics(chat, 'task')['context']
    assert compacting['input_tokens'] == 400 and compacting['stale'] and compacting['compaction_pending']
    metadata['model_requests'].append(request(80, 5))
    usage.publish_live_usage(ops.project_info().path, chat, 'task', metadata)
    current = ops.read_usage_statistics(chat, 'task')['context']
    assert current['input_tokens'] == 80 and not current['compaction_pending']
    assert current['context_window'] == 1000 and current['compression_threshold'] == 800


def test_native_estimate_has_distinct_source_and_live_path_uses_cached_history(ledger, monkeypatch):
    ops, chat = ledger
    metadata = {'usage_turn_id': 'estimate', 'model_requests': [request()], 'native_context': {'latest_input_tokens': 100, 'current_estimate_tokens': 135, 'current_estimate_source': 'sdk_projection', 'stale': False, 'state': 'waiting'}}
    ops.read_usage_statistics(chat)
    monkeypatch.setattr(usage, '_read_json', lambda path: pytest.fail('live event must not scan audit records'))
    result = usage.publish_live_usage(ops.project_info().path, chat, 'task', metadata)
    assert result['context']['input_tokens'] == 135 and result['context']['source'] == 'estimated'
    assert result['context']['last_generation_input_tokens'] == 100 and not result['context']['stale']
    assert result['data_updated_at'] and result['data_sequence'] > 0


def test_turns_in_one_task_and_other_chats_are_separate(ledger):
    ops, chat = ledger
    other = ChatStore(ops.project_info().path).create('另一个聊天')['id']
    audit(ops, chat, 'task', [{'usage_turn_id': 'a', 'model_requests': [request(10, 2)]}, {'usage_turn_id': 'b', 'model_requests': [request(20, 3)]}])
    audit(ops, other, 'foreign', [{'model_requests': [request(900, 90)]}])
    result = ops.read_usage_statistics(chat, 'task')
    assert result['task_usage']['requests'] == 2 and result['task_usage']['total_tokens'] == 35
    assert ops.read_usage_statistics(other)['chat_usage']['total_tokens'] == 990


def test_reads_do_not_retimestamp_actual_data_and_empty_context_is_unknown(ledger):
    ops, chat = ledger
    before = {str(p): p.read_bytes() for p in Path(ops.project_info().path).parent.rglob('*') if p.is_file()}
    empty = ops.read_usage_statistics(chat)
    again = ops.read_usage_statistics(chat)
    assert empty['context']['input_tokens'] is None and empty['context']['source'] == 'unknown'
    assert empty['data_updated_at'] == again['data_updated_at'] is None
    assert all(Path(p).read_bytes() == content for p, content in before.items())


def test_failed_request_keeps_last_known_input_without_claiming_current_actual(ledger):
    ops, chat = ledger
    metadata = {'usage_turn_id': 'failed', 'model_requests': [request(857377, 10, usage_sampled_at='2026-11-05T01:00:00+00:00'), {'purpose': 'generation', 'complete': False, 'usage': None}],
                'native_context': {'latest_input_tokens': 857377, 'current_estimate_tokens': None, 'stale': True, 'state': 'waiting'}}
    result = usage.publish_live_usage(ops.project_info().path, chat, 'task', metadata)
    context = result['context']
    assert context['input_tokens'] is None and context['source'] == 'unknown'
    assert context['last_known_input_tokens'] == context['last_generation_input_tokens'] == 857377
    assert context['last_known_sampled_at'] == '2026-11-05T01:00:00+00:00'
    assert not context['last_known_is_current_request'] and context['stale']


def test_one_sdk_turn_is_not_charged_twice_when_durable_task_ownership_arrives(ledger):
    ops, chat = ledger
    metadata = {'usage_turn_id': 'one-real-turn', 'model_requests': [request()]}
    usage.publish_live_usage(ops.project_info().path, chat, 'headless-turn', metadata)
    audit(ops, chat, 'actual-task', [metadata])
    result = ops.read_usage_statistics(chat, 'actual-task')
    assert result['chat_usage']['requests'] == result['task_usage']['requests'] == 1
    assert result['task_usage']['total_tokens'] == 120


def test_large_content_never_enters_statistics_caches_or_snapshots(ledger):
    ops, chat = ledger
    large = 'PRIVATE_RAW_AND_ARGUMENTS_SENTINEL' * 20000
    ChatStore(ops.project_info().path).append(chat, 'assistant', large)
    metadata = {'usage_turn_id': 'light-only', 'model_requests': [request(raw=large, tool_calls=[{'arguments': large}])], 'raw': large, 'model_messages': [{'text': large}], 'prompt_config': {'texts': {'system': large}}}
    path = audit(ops, chat, 'task-light', [metadata])
    original = path.read_bytes()
    result = ops.read_usage_statistics(chat, 'task-light')
    assert result['task_usage']['total_tokens'] == 120
    usage.publish_live_usage(ops.project_info().path, chat, 'task-light', metadata)
    for cached in (usage._FILES[str(path)][2], usage._HISTORY[(str(layout(ops.project_info().path).db.resolve()), chat)], usage._LIVE[(str(layout(ops.project_info().path).db.resolve()), chat, 'task-light')], result):
        serialized = json.dumps(cached)
        assert 'PRIVATE_RAW_AND_ARGUMENTS_SENTINEL' not in serialized
        assert len(serialized) < 10000
    assert path.read_bytes() == original


def test_headless_turn_metadata_survives_registry_restart_and_deduplicates_native_audit(ledger):
    ops, chat = ledger
    metadata = {'chat_id': chat, 'usage_turn_id': 'headless-sdk-turn', 'model_requests': [request(70, 8)], 'raw': 'never cache content'}
    path = layout(ops.project_info().path).extraction / 'assistant' / 'chats' / chat / 'turns' / 'headless-sdk-turn' / 'metadata.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata), encoding='utf-8')
    usage.publish_live_usage(ops.project_info().path, chat, 'headless-sdk-turn', metadata)
    usage._LIVE.clear()
    usage._FILES.clear()
    usage._HISTORY.clear()
    result = ops.read_usage_statistics(chat)
    assert result['chat_usage']['requests'] == 1 and result['chat_usage']['total_tokens'] == 78
    audit(ops, chat, 'native-task', [metadata])
    combined = ops.read_usage_statistics(chat, 'native-task')
    assert combined['chat_usage']['requests'] == combined['task_usage']['requests'] == 1
    assert combined['task_usage']['total_tokens'] == 78


def test_durable_turn_without_identity_does_not_duplicate_authoritative_task_audit(ledger):
    ops, chat = ledger
    metadata = {'chat_id': chat, 'model_requests': [request(70, 8)]}
    audit(ops, chat, 'task-authority', [metadata])
    path = layout(ops.project_info().path).extraction / 'assistant' / 'chats' / chat / 'turns' / 'directory-is-not-identity' / 'metadata.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metadata), encoding='utf-8')
    before = path.read_bytes()
    result = ops.read_usage_statistics(chat, 'task-authority')
    assert result['chat_usage']['requests'] == result['task_usage']['requests'] == 1
    assert result['chat_usage']['total_tokens'] == 78
    assert result['chat_usage']['incomplete'] and result['task_usage']['incomplete']
    assert any(d['code'] == 'usage_source_identity_missing' for d in result['diagnostics'])
    assert path.read_bytes() == before


def test_slow_history_io_does_not_block_live_registry_publication(ledger, monkeypatch):
    import threading

    ops, chat = ledger
    other = ChatStore(ops.project_info().path).create('实时另一个聊天')['id']
    ops.read_usage_statistics(other)
    audit(ops, chat, 'slow-history', [{'model_requests': [request()]}])
    entered, release, published = threading.Event(), threading.Event(), threading.Event()
    original = usage._read_json
    errors = []
    def slow_read(path):
        entered.set()
        assert release.wait(3)
        return original(path)
    def historical():
        try:
            ops.read_usage_statistics(chat)
        except BaseException as exc:
            errors.append(exc)
    def live():
        try:
            usage.publish_live_usage(ops.project_info().path, other, 'live', {'usage_turn_id': 'live-turn', 'model_requests': [request()]})
            published.set()
        except BaseException as exc:
            errors.append(exc)
    monkeypatch.setattr(usage, '_read_json', slow_read)
    reader = threading.Thread(target=historical, daemon=True)
    publisher = threading.Thread(target=live, daemon=True)
    try:
        reader.start()
        assert entered.wait(1)
        publisher.start()
        assert published.wait(1), 'Historical disk I/O must not hold the registry lock or block SDK callbacks'
    finally:
        release.set()
        reader.join(3)
        if publisher.ident is not None:
            publisher.join(3)
    assert not errors


def test_first_live_event_never_loads_transcript_or_history(ledger, monkeypatch):
    ops, chat = ledger
    def forbidden(*args, **kwargs):
        pytest.fail('First SDK usage event must not perform historical disk work')
    monkeypatch.setattr(ChatStore, 'load', forbidden)
    monkeypatch.setattr(usage, '_read_json', forbidden)
    result = usage.publish_live_usage(ops.project_info().path, chat, 'task', {'usage_turn_id': 'first-live', 'model_requests': [request()]})
    assert result['history_pending'] and result['chat_usage']['total_tokens'] is None
    assert result['chat_usage']['known_totals']['total_tokens'] == 120
    assert result['task_usage']['total_tokens'] == 120

def cache_request(input_tokens, cache_read):
    raw = {'prompt_tokens': input_tokens, 'completion_tokens': 0}
    if cache_read is not None:
        raw['prompt_tokens_details'] = {'cached_tokens': cache_read}
    return {'complete': True, 'usage': raw}


def test_cache_rate_is_weighted_by_same_request_input_not_average_percentages():
    result = usage._aggregate([cache_request(100, 90), cache_request(900, 90)])
    assert result['cache_hit_rate'] == result['known_cache_hit_rate'] == .18
    assert result['cache_covered_input_tokens'] == 1000 and result['cache_covered_read_tokens'] == 180
    assert result['cache_covered_requests'] == 2 and result['cache_unknown_requests'] == 0
    assert result['uncached_input_tokens'] == result['known_uncached_input_tokens'] == 820
    assert result['cache_write_tokens'] is None  # cache writes are not required to know read/input rate


def test_partial_cache_coverage_never_treats_missing_or_unknown_input_as_zero():
    result = usage._aggregate([cache_request(100, 90), cache_request(900, None),
                              {'complete': False, 'usage': {'cache_read_tokens': 50}}])
    assert result['cache_hit_rate'] is None and result['known_cache_hit_rate'] == .9
    assert result['cache_covered_requests'] == 1 and result['cache_unknown_requests'] == 2
    assert result['cache_covered_input_tokens'] == 100 and result['cache_covered_read_tokens'] == 90
    assert result['known_totals']['cache_read_tokens'] == 140  # unpaired cache is excluded from rate numerator
    assert result['uncached_input_tokens'] is None and result['known_uncached_input_tokens'] == 10


@pytest.mark.parametrize('invalid', [-1, 101, True])
def test_invalid_cache_counter_does_not_create_a_false_rate(invalid):
    result = usage._aggregate([cache_request(100, invalid), cache_request(100, 0)])
    assert result['cache_hit_rate'] is None and result['known_cache_hit_rate'] == 0
    assert result['cache_covered_requests'] == 1 and result['cache_unknown_requests'] == 1
    assert result['cache_invalid_requests'] == (1 if invalid == 101 else 0)
    assert result['known_totals']['cache_read_tokens'] == 0


def test_zero_input_has_known_coverage_without_inventing_a_zero_percent_rate():
    result = usage._aggregate([cache_request(0, 0)])
    assert result['cache_hit_rate'] is None and result['known_cache_hit_rate'] is None
    assert result['cache_covered_requests'] == 1 and result['cache_unknown_requests'] == 0
    assert result['uncached_input_tokens'] == 0
    empty = usage._aggregate([])
    assert empty['cache_hit_rate'] is None and empty['known_uncached_input_tokens'] is None


def test_messages_and_openai_cache_rates_use_same_inclusive_input():
    first = {'complete': True, 'usage': {'prompt_tokens': 150, 'completion_tokens': 1, 'prompt_tokens_details': {'cached_tokens': 40}}}
    second = {'complete': True, 'usage': {'input_tokens': 100, 'output_tokens': 1, 'cache_read_input_tokens': 40, 'cache_creation_input_tokens': 10}}
    assert usage._aggregate([first, second])['cache_hit_rate'] == pytest.approx(80/300)


def test_ops_chat_cache_is_weighted_and_history_pending_only_exposes_known_subset(ledger):
    ops, chat = ledger
    turn = {'usage_turn_id': 'cache-turn-1', 'model_requests': [cache_request(100, 90)]}
    usage.publish_live_usage(ops.project_info().path, chat, 'cache-task-1', turn)
    # No history has been loaded; current live subset is known, full-chat exact rate is not.
    first = usage.read_statistics(ops.project_info().path, chat, 'cache-task-1', _refresh_history=False)
    assert first['history_pending'] and first['chat_usage']['cache_hit_rate'] is None
    assert first['chat_usage']['cache_unknown_requests'] is None and first['chat_usage']['known_cache_hit_rate'] == .9
    audit(ops, chat, 'cache-task-1', [turn])
    audit(ops, chat, 'cache-task-2', [{'usage_turn_id': 'cache-turn-2', 'model_requests': [cache_request(900, 90)]}])
    final = ops.read_usage_statistics(chat, 'cache-task-2')
    assert final['chat_usage']['cache_hit_rate'] == .18 and final['task_usage']['cache_hit_rate'] == .1
    assert ops.read_usage_statistics(chat)['chat_usage']['cache_hit_rate'] == .18  # local statistics cache adds no hits
