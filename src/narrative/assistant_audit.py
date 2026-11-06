"""Host write payloads execute unchanged but do not become transcript/audit copies."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _host_write(value):
    if isinstance(value, list):
        return any(_host_write(item) for item in value)
    if isinstance(value, dict):
        return value.get('name', value.get('tool')) == 'write_host_file' or any(_host_write(item) for item in value.values())
    return False


def _safe_arguments(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    try:
        arguments = json.loads(value) if isinstance(value, str) else dict(value)
    except (TypeError, ValueError):
        return {'arguments_omitted': True, 'character_count': len(raw), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    if not isinstance(arguments, dict):
        return {'arguments_omitted': True, 'character_count': len(raw), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}
    if arguments.get('content_omitted'):
        return arguments
    if 'content' in arguments:
        content = arguments.pop('content')
        encoded = content.encode() if isinstance(content, str) else json.dumps(content, ensure_ascii=False).encode()
        arguments.update(content_omitted=True, content_bytes=len(encoded), content_sha256=hashlib.sha256(encoded).hexdigest())
    return arguments


def sanitize_host_audit(value: Any) -> Any:
    """Return a separate safe projection, never repair or alter execution arguments."""
    if isinstance(value, list):
        return [sanitize_host_audit(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: sanitize_host_audit(item) for key, item in value.items()}
    if value.get('name', value.get('tool')) == 'write_host_file':
        for field in ('args', 'arguments', 'arguments_json'):
            if field in value:
                safe = _safe_arguments(value[field])
                result[field] = json.dumps(safe, ensure_ascii=False) if isinstance(value[field], str) else safe
        if 'argument_fragments' in value:
            result['argument_fragments'] = ['[host write payload omitted]']
    if _host_write(value):
        for field in ('raw', 'raw_response'):
            if field in value:
                result[field] = '[raw host-write payload omitted; structured receipt retained]'
        if 'full_output' in value and isinstance(result.get('content'), list):
            result['full_output'] = json.dumps(result['content'], ensure_ascii=False)
    return result
