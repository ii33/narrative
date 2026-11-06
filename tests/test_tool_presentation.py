"""Program-derived tool names and bounded actual output, independent of model prose."""
import copy

from narrative.assistant import _public
from narrative.tool_presentation import PREVIEW_CHARACTERS, tool_presentation


def test_staging_feedback_preserves_exact_problem_locations_without_echoing_draft():
    step = {'tool': 'patch_extraction_submission', 'ok': True,
            'args': {'submission_id': 'extraction-submission-1', 'patches': []},
            'result': {'status': 'staged', 'submission_id': 'extraction-submission-1',
                       'problems': [{'section': 'nodes', 'index': 2, 'reference': '@3',
                                     'field': 'quotes', 'message': '缺少引文'}]}}
    shown = tool_presentation(step)
    assert shown['display_name'] == '修正暂存提取'
    assert '"index": 2' in shown['output_text'] and '@3' in shown['output_text']
    assert '缺少引文' in shown['output_text'] and not shown['output_truncated']


def test_tool_receipt_formats_nested_output_without_mutating_audit():
    step = {'tool': 'read_host_command', 'ok': True, 'args': {'command_id': 'command-1'},
            'result': {'status': 'completed', 'content': '第一行\n第二行', 'exit_code': 0}}
    before = copy.deepcopy(step)
    result = _public([step])[0]
    assert result['display_name'] == '读取命令输出'
    assert '\n  "status": "completed"' in result['output_text']
    assert '第一行' in result['output_text']
    assert 'command-1' in result['arguments_text']
    assert not result['output_truncated'] and step == before
    assert 'result' not in result


def test_large_output_is_explicitly_clipped_and_failure_is_visible():
    result = tool_presentation({'tool': 'unregistered_extension_tool', 'ok': True,
                                'result': '文' * (PREVIEW_CHARACTERS+1)})
    assert result['display_name'] == 'unregistered_extension_tool'
    assert len(result['output_text']) == PREVIEW_CHARACTERS and result['output_truncated']
    failed = tool_presentation({'tool': 'save_asset', 'ok': False, 'error': '修订冲突'})
    assert failed['output_text'] == '修订冲突'
    assert failed['display_name'] != '工具调用'


def test_host_write_previews_do_not_reintroduce_omitted_payload():
    step = {'tool': 'write_host_file', 'ok': True,
                                'args': {'path': 'outside.bin', 'encoding': 'base64', 'content': 'c2VjcmV0'},
                                'result': {'bytes_written': 6}}
    result = tool_presentation(step)
    assert 'c2VjcmV0' not in result['arguments_text']
    assert 'content_omitted' in result['arguments_text']
    assert 'content' not in _public([step])[0]['args']
