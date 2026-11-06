"""Bounded, deterministic tool previews for the conversation panel."""
from __future__ import annotations

import json

PREVIEW_CHARACTERS = 12000
_LABELS = {
    'run_host_command': '执行系统命令', 'read_host_command': '读取命令输出',
    'stop_host_command': '停止系统命令', 'read_host_file': '读取文件',
    'write_host_file': '写入文件', 'list_host_directory': '查看目录',
    'read_source': '查看原文', 'list_sources': '查看原文列表',
    'locate_source': '定位引文', 'read_source_slice': '查看切片原文',
    'list_nodes': '查看节点', 'get_node': '查看节点详情',
    'submit_extraction': '提交提取', 'preview_extraction': '检查提取',
    'read_extraction_submission': '查看暂存提取',
    'patch_extraction_submission': '修正暂存提取',
    'list_content_submissions': '查找暂存内容',
    'read_content_submission': '查看暂存内容',
    'patch_content_submission': '修正暂存内容',
}


def tool_display_name(name):
    from .tool_api import TOOLS

    if name in _LABELS:
        return _LABELS[name]
    tool = TOOLS.get(name)
    if tool:
        # Tool registration is the authority, not model-authored narration.
        title = tool.summary.split('；', 1)[0].split('。', 1)[0].split('\n', 1)[0]
        return title[:36] + ('…' if len(title) > 36 else '')
    return str(name or '未命名工具')


def _preview(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            pass
    if isinstance(value, str):
        return value[:PREVIEW_CHARACTERS], len(value) > PREVIEW_CHARACTERS
    chunks, size = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, default=str)
    for chunk in encoder.iterencode(value):
        remaining = PREVIEW_CHARACTERS-size
        chunks.append(chunk[:remaining])
        size += len(chunk)
        if size > PREVIEW_CHARACTERS:
            return ''.join(chunks), True
    return ''.join(chunks), False


def tool_presentation(step):
    from .assistant_audit import sanitize_host_audit

    safe = sanitize_host_audit(step)
    arguments, args_clipped = _preview(safe.get('args') or {})
    if safe.get('ok') is False:
        output = safe.get('error') or '调用失败，未返回结果'
    elif 'result' in safe:
        output = safe['result']
    else:
        output = safe.get('summary') or '等待工具结果'
    output, clipped = _preview(output)
    return {'display_name': tool_display_name(step.get('tool')),
            'arguments_text': arguments, 'arguments_truncated': args_clipped,
            'output_text': output, 'output_truncated': clipped}
