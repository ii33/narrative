"""Native next-step message admission and presentation-only delivery receipts."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading


def pack_instruction(instruction, *extra_text):
    blocks = [{'type': 'text', 'text': instruction}] if isinstance(instruction, str) else copy.deepcopy(instruction)
    if not isinstance(blocks, list):
        raise ValueError('模型输入须为文本或内容块数组')
    result = []
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('模型内容块须为对象')
        if block.get('type') == 'text' and isinstance(block.get('text'), str):
            result.append({'type': 'text', 'text': block['text']})
            continue
        if block.get('type') == 'image_url':
            image = block.get('image_url')
            if not isinstance(image, dict):
                raise ValueError('图片内容块需要image_url对象')
            url = image.get('url', '')
            if not isinstance(url, str) or not url.startswith('data:image/') or ';base64,' not in url:
                raise ValueError('图片须先作为本地附件导入并提供base64图像')
            prefix, data = url.split(';base64,', 1)
            mime = prefix.removeprefix('data:')
        elif block.get('type') == 'image' and isinstance(block.get('data'), str):
            data, mime = block['data'], block.get('mimeType')
        else:
            raise ValueError('仅支持文本和已导入图片内容块')
        if mime not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
            raise ValueError('图片媒体类型不受支持')
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('图片base64无效') from exc
        if not decoded:
            raise ValueError('图片内容为空')
        result.append({'type': 'image', 'data': data, 'mimeType': mime})
    result.extend({'type': 'text', 'text': text} for text in extra_text if isinstance(text, str) and text)
    return result


def instruction_audit(blocks):
    safe = []
    for block in blocks:
        if block.get('type') == 'image':
            raw = base64.b64decode(block['data'])
            safe.append({'type': 'image', 'mimeType': block['mimeType'], 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        else:
            safe.append(block)
    return json.dumps(safe, ensure_ascii=False, indent=2)


class SteeringLedger:
    def __init__(self):
        self.lock = threading.RLock()
        self.records = {}

    def add(self, message_id, chat_id, task_id, callback):
        with self.lock:
            if message_id in self.records:
                raise ValueError('插话消息已提交，不能重复入队')
            self.records[message_id] = {'message_id': message_id, 'chat_id': chat_id, 'task_id': task_id, 'status': 'sending', 'callback': callback}

    def update(self, message_id, status, reason=None):
        with self.lock:
            record = self.records.get(message_id)
            if not record or record['status'] in {'delivered', 'not_delivered'}:
                return
            if status == 'queued' and record['status'] != 'sending':
                return
            record['status'] = status
            event = {key: value for key, value in record.items() if key != 'callback'}
            if reason:
                event['reason'] = reason
            callback = record['callback']
        if callback is not None:
            try:
                callback(event)
            except Exception:
                pass

    def pending(self, task_id):
        with self.lock:
            return [key for key, row in self.records.items() if row['task_id'] == task_id and row['status'] in {'sending', 'queued'}]
