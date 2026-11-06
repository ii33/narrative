"""MiniMax H3 adapter: explicit reference-motion or endpoint-frame intent.

Contract: https://platform.minimax.io/docs/api-reference/video-generation-v2-create
Task query: https://platform.minimax.io/docs/api-reference/video-generation-v2-query
No submission retry, no total-task deadline, and no credentials in receipts.
"""
from __future__ import annotations

import base64
import http.client
import json
import mimetypes
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

MODELS = ('MiniMax-H3', 'MiniMax-H3-Highspeed', 'MiniMax-H3-Lite', 'MiniMax-H3-Max')


def validate_options(model, resolution, duration_ms):
    if model not in MODELS:
        raise ValueError('请选择支持的 MiniMax H3 视频模型')
    resolutions = ('768P', '2K') if model == 'MiniMax-H3' else ('480P', '768P')
    if resolution not in resolutions:
        raise ValueError('该模型的分辨率可选：' + '、'.join(resolutions))
    minimum = 5000 if model == 'MiniMax-H3-Max' else 4000
    if type(duration_ms) is not int or not minimum <= duration_ms <= 15000 or duration_ms % 1000:
        raise ValueError(f'该服务生成时长须为{minimum // 1000}至15整秒；请显式选择生成时长，原镜头时长不会自动改变')


def _image(path):
    from PIL import Image
    file = Path(path)
    with Image.open(file) as image:
        image.verify()
        width, height = image.size
    if not 256 <= min(width, height) or max(width, height) > 5760 or not .4 <= width / height <= 2.5:
        raise ValueError('视频参考图每边须为256..5760像素，宽高比0.4..2.5；请显式调整素材')
    return _data_url(file, 30 * 1024 * 1024)


def _data_url(path, limit):
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise ValueError('视频生成参考素材不存在、为空或超出服务文件大小限制')
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    return 'data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def build_request(*, model, resolution, duration_ms, mode, prompt, images, motion_path=None):
    validate_options(model, resolution, duration_ms)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('请填写本镜视频生成说明')
    content: list[dict] = [{'type': 'text', 'text': prompt}]
    if mode == 'reference_motion':
        if motion_path is None or not images or len(images) > 9:
            raise ValueError('参考运动模式需要干净草图动画和1至9张风格参考帧')
        content.append({'type': 'video_url', 'video_url': {'url': _data_url(motion_path, 50 * 1024 * 1024)}, 'role': 'reference_video'})
        content.extend({'type': 'image_url', 'image_url': {'url': _image(path)}, 'role': 'reference_image'} for path in images)
    elif mode == 'endpoints':
        if motion_path is not None or not 1 <= len(images) <= 2:
            raise ValueError('首尾帧模式只接受1张首帧或首尾各1张，不与运动参考混用')
        content.extend({'type': 'image_url', 'image_url': {'url': _image(path)}, 'role': role}
                       for path, role in zip(images, ('first_frame', 'last_frame'), strict=False))
    else:
        raise ValueError('生成意图须为reference_motion或endpoints')
    request = {'model': model, 'resolution': resolution, 'duration': duration_ms // 1000,
               'ratio': '16:9' if mode == 'reference_motion' else 'adaptive', 'content': content}
    if len(json.dumps(request).encode()) > 64 * 1024 * 1024:
        raise ValueError('生成请求超过64MB，请减少或缩小参考素材')
    return request


def validate_base_url(base_url):
    if not isinstance(base_url, str) or not base_url:
        raise ValueError('视频生成服务地址无效')
    address = urlsplit(base_url)
    local = address.hostname in {'localhost', '127.0.0.1', '::1'}
    if address.scheme != 'https' and not (local and address.scheme == 'http'):
        raise ValueError('视频生成服务须使用HTTPS地址')
    if not address.hostname or address.username or address.password or address.query or address.fragment:
        raise ValueError('视频生成服务地址无效')
    return address


def _request(base_url, api_key, method, route, payload=None):
    if not api_key:
        raise ValueError('尚未配置视频生成服务密钥')
    address = validate_base_url(base_url)
    assert address.hostname is not None
    connection_type = http.client.HTTPSConnection if address.scheme == 'https' else http.client.HTTPConnection
    connection = connection_type(address.hostname, address.port, timeout=60)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(method, address.path.rstrip('/') + route, body,
                           {'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read()
        if response.status >= 300:
            message = raw[:2000].decode(errors='replace').replace(api_key, '[REDACTED]')
            raise ValueError(f'视频服务返回{response.status}：{message}')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('视频服务返回的状态格式无效')
        return data
    except (OSError, http.client.HTTPException) as error:
        raise ValueError('视频服务连接失败：' + str(error).replace(api_key, '[REDACTED]')) from error
    finally:
        connection.close()


def submit(base_url, api_key, request):
    result = _request(base_url, api_key, 'POST', '/v2/video_generation', request)
    if not isinstance(result.get('task_id'), str) or not result['task_id']:
        raise ValueError('视频服务未返回有效任务编号，不自动重新提交')
    return result


def query(base_url, api_key, task_id):
    result = _request(base_url, api_key, 'GET', '/v2/query/video_generation/' + quote(task_id, safe=''))
    if not isinstance(result.get('task'), dict) or result['task'].get('id') != task_id:
        raise ValueError('视频服务任务响应与请求编号不一致')
    return result


class _HttpsRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ValueError('视频下载重定向须使用HTTPS')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, destination):
    if not isinstance(url, str) or urlsplit(url).scheme != 'https':
        raise ValueError('返回的视频须使用HTTPS下载地址')
    # No provider Authorization header is sent to the returned CDN.
    with build_opener(_HttpsRedirects()).open(url, timeout=60) as source, Path(destination).open('xb') as output:
        while data := source.read(1024 * 1024):
            output.write(data)
