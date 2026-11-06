"""Offline lazy-tool QA validators and wire-capture privacy checks."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def acceptance(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location('tool_loading_acceptance', scripts / 'accept_tool_loading.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_initial_omission_then_load_then_read_passes(acceptance):
    requests = [{'tool_names': ['load_tools']}, {'tool_names': ['load_tools', 'read_screenplay']}]
    steps = [{'tool': 'list_available_tools', 'ok': True}, {'tool': 'load_tools', 'ok': True}, {'tool': 'read_screenplay', 'ok': True}]
    assert all(acceptance.assess_loading(requests, steps, ['same-session', 'same-session']).values())


def test_initial_large_catalog_or_unloaded_storyboard_exposure_fails(acceptance):
    requests = [{'tool_names': ['read_screenplay', 'apply_storyboard_changes']}]
    checks = acceptance.assess_loading(requests, [], ['session'])
    assert not checks['initial_catalog_omits_read_screenplay']
    assert not checks['unloaded_large_storyboard_tools_hidden']
    assert not checks['load_before_business_read']


def test_restarted_session_or_read_before_load_fails(acceptance):
    requests = [{'tool_names': []}, {'tool_names': ['read_screenplay']}]
    steps = [{'tool': 'read_screenplay', 'ok': True}, {'tool': 'load_tools', 'ok': True}]
    checks = acceptance.assess_loading(requests, steps, ['session-one', 'session-two'])
    assert not checks['same_sdk_session']
    assert not checks['load_before_business_read']


def test_capture_records_only_tool_names_and_size_without_network(acceptance, monkeypatch):
    from narrative import model_gateway
    sent = []

    def fake_request(self, method, url, body=None, headers=None, **kwargs):
        sent.append(body)

    monkeypatch.setattr(model_gateway._UpstreamHTTPConnection, 'request', fake_request)
    monkeypatch.setattr(model_gateway._UpstreamHTTPSConnection, 'request', fake_request)
    body = json.dumps({'tools': [{'function': {'name': 'read_screenplay'}}],
        'messages': [{'content': 'private source'}]}).encode()
    records = []
    with acceptance.capture_provider_tools(records):
        connection = model_gateway._UpstreamHTTPConnection('localhost')
        connection.request('POST', '/chat/completions', body, {'Authorization': 'private-key'})
    assert sent == [body]
    assert records == [{'index': 1, 'tool_names': ['read_screenplay'], 'body_bytes': len(body)}]
    assert 'private' not in json.dumps(records)
    assert model_gateway._UpstreamHTTPConnection.request is fake_request


def sdk_rows(result, *, is_error=False, arguments='{}', response_id='list-call'):
    return [{'data': {'message': {'role': 'assistant', 'content': [
        {'type': 'tool-call', 'id': 'list-call', 'name': 'read_screenplay', 'arguments': arguments}]}}},
        {'data': {'message': {'role': 'tool', 'toolCallId': response_id, 'isError': is_error,
            'content': [{'type': 'text', 'text': json.dumps(result)}]}}}]


def test_sdk_list_response_requires_matching_call_and_nonerror(acceptance):
    expected = [{'id': 'screenplay-sample', 'title': 'Sample'}]
    checks, responses = acceptance.assess_sdk_tool_responses(sdk_rows(expected), expected)
    assert checks['sdk_received_actual_list_without_error']
    assert responses[0]['actual_fixture_list']
    for rows in [sdk_rows(expected, is_error=True), sdk_rows(expected, response_id='other-call'),
                 sdk_rows({'screenplay': expected[0]}), sdk_rows(expected, arguments='{"screenplay_id":"screenplay-sample"}')]:
        checks, _ = acceptance.assess_sdk_tool_responses(rows, expected)
        assert not checks['sdk_received_actual_list_without_error']


def test_sdk_rejected_object_schema_is_not_mistaken_for_host_success(acceptance):
    expected = [{'id': 'screenplay-sample'}]
    rows = sdk_rows('Error: returned invalid output: value must be an object', is_error=True)
    checks, responses = acceptance.assess_sdk_tool_responses(rows, expected)
    assert not checks['sdk_received_actual_list_without_error']
    assert responses[0]['is_error'] is True


def test_sdk_empty_list_and_explicit_null_are_valid(acceptance):
    checks, _ = acceptance.assess_sdk_tool_responses(sdk_rows([], arguments='{"screenplay_id":null}'), [])
    assert checks['sdk_received_actual_list_without_error']


def test_schema_bridge_abort_ignores_business_and_user_text(acceptance):
    error = 'Error: tool "read_screenplay" returned invalid output: "value" must be an object'
    assert acceptance.sdk_output_schema_errors([{'role': 'assistant', 'content': error}]) == []
    assert acceptance.sdk_output_schema_errors([{'role': 'tool', 'content': 'Error: screenplay对象不存在'}]) == []
    assert acceptance.sdk_output_schema_errors([{'role': 'tool', 'content': 'field description mentions returned invalid output'}]) == []
    found = acceptance.sdk_output_schema_errors([{'role': 'tool', 'tool_call_id': 'list-call', 'content': error}])
    assert found == [{'code': 'sdk_tool_output_schema_rejected', 'tool': 'read_screenplay',
        'tool_call_id': 'list-call', 'reason': 'returned invalid output'}]
    assert acceptance.sdk_output_schema_errors([{'role': 'tool', 'content': [
        {'type': 'text', 'text': 'Error: tool "read_screenplay" returned invalid output schema: mismatch'}]}])


def test_external_abort_is_qa_only_and_records_specific_reason(acceptance, monkeypatch):
    monkeypatch.setattr(acceptance, 'PROVIDER_REQUESTS', [])
    assert acceptance.external_abort_reason() is None
    acceptance.PROVIDER_REQUESTS.append({'sdk_output_schema_errors': [{'tool': 'read_screenplay'}]})
    assert 'read_screenplay' in acceptance.external_abort_reason()
    assert 'no retry' in acceptance.external_abort_reason()


def test_common_qa_abort_callback_defaults_to_disabled(acceptance):
    import inspect
    assert inspect.signature(acceptance.qa.main).parameters['external_abort'].default is None


def test_usage_breakdown_preserves_unknown_and_cache_is_subset(acceptance):
    usage = {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105,
        'prompt_tokens_details': {'cached_tokens': 80}}
    result = acceptance.usage_breakdown([{'model_requests': [{'usage': usage}]}])
    assert [result[k] for k in ['total_input', 'cache_hit', 'uncached_known', 'output', 'total']] == [100, 80, 20, 5, 105]
    result = acceptance.usage_breakdown([{'model_requests': [{'usage': usage}, {'usage': None}]}])
    assert result['total_input'] is None and result['cache_hit'] is None and result['total'] is None
    assert result['uncached_known'] == 20 and result['missing_usage_requests'] == 1
    empty = acceptance.usage_breakdown([])
    assert all(empty[k] is None for k in ['total_input', 'cache_hit', 'uncached_known', 'output', 'total'])


def test_seed_and_recorded_source_share_same_text(acceptance):
    import inspect
    assert "ops.import_source('按需工具只读样本', TEXT)" in inspect.getsource(acceptance.seed)
    assert 'qa.TEXT = seed, TASK, TEXT' in inspect.getsource(acceptance.main)
