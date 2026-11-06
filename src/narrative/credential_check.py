"""Explicit, one-request credential checks without changing chats or settings."""
from __future__ import annotations

import http.client
import json
from urllib.parse import urlsplit
from uuid import uuid4

from .harness import HarnessSettings, resolve_key
from .model_gateway import _UpstreamHTTPConnection, _UpstreamHTTPSConnection, adapt_request


def _redacted_result(result: dict, key: str) -> dict:
    def clean(value):
        if isinstance(value, str):
            return value.replace(key, "[REDACTED]") if key else value
        if isinstance(value, dict):
            return {clean(k): clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return {clean(k): clean(v) for k, v in result.items()}


def check_model_credential(ops, profile: dict, supplied_key: str | None = None, *, ignore_saved_key: bool = False) -> dict:
    """Validate the selected endpoint, not a provider-independent key identity."""
    result = {'valid': None, 'code': 'configuration_error', 'message': '模型配置无效',
              'http_status': None, 'usage': None, 'checked_scope': None}
    connection = None
    key = ""
    try:
        settings = HarnessSettings.model_validate(profile)
        key = resolve_key(settings, supplied_key, ops=None if ignore_saved_key else ops)
        url = urlsplit(settings.base_url)
        assert url.hostname is not None
        result['checked_scope'] = {'provider': settings.provider, 'base_url': settings.base_url, 'model': settings.model}
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'Authorization': 'Bearer '+key}
        if settings.provider == 'opencode-go':
            headers.update({'x-opencode-session': 'credential-check-'+uuid4().hex, 'User-Agent': 'narrative-core/0.1.0'})
        body = adapt_request({'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'stream': False},
                             settings.model_copy(update={'max_tokens': 64}))
        connection_type = _UpstreamHTTPSConnection if url.scheme == 'https' else _UpstreamHTTPConnection
        connection = connection_type(url.hostname, url.port, timeout=min(settings.timeout_seconds, 30))
        connection.request('POST', url.path.rstrip('/')+'/chat/completions', json.dumps(body).encode(), headers)
        response = connection.getresponse()
        result['http_status'] = response.status
        # Bounded response consumption; never expose server error text or headers.
        raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            result.update(code='invalid_response', message='服务响应过大，未能确认有效性')
            return _redacted_result(result, key)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            payload = None
        status = response.status
        if 200 <= status < 300:
            choices = payload.get('choices') if isinstance(payload, dict) else None
            valid = bool(isinstance(choices, list) and choices and isinstance(choices[0], dict)
                         and isinstance(choices[0].get('message'), dict)
                         and (isinstance(choices[0]['message'].get('content'), str)
                              or (choices[0].get('finish_reason') == 'length'
                                  and isinstance(choices[0]['message'].get('reasoning_content'), str))))
            result.update(valid=True if valid else None, code='valid' if valid else 'invalid_response',
                          message='密钥与当前模型配置可用' if valid else '服务未返回有效对话响应，未能确认有效性')
            usage = payload.get('usage') if isinstance(payload, dict) else None
            if isinstance(usage, dict):
                # Only numerical provider usage fields are public, never echoed text.
                result['usage'] = {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        elif status in {401, 403}:
            result.update(valid=False, code='authentication_failed' if status == 401 else 'permission_denied',
                          message='密钥认证失败' if status == 401 else '当前密钥无权访问此模型')
        elif status in {400, 404}:
            result.update(code='configuration_error', message='服务地址、模型或请求配置不匹配，未能确认密钥有效性')
        elif status == 429:
            result.update(code='rate_limited', message='服务限流，暂时无法确认有效性')
        else:
            result.update(code='service_error', message='服务异常，暂时无法确认有效性')
    except ValueError:
        result.update(code='configuration_error', message='模型配置或本机凭据不可用，请检查后重试')
    except (OSError, TimeoutError, http.client.HTTPException):
        result.update(code='network_error', message='网络连接失败或响应超时，未能确认有效性')
    finally:
        if connection is not None:
            connection.close()
    return _redacted_result(result, key)
