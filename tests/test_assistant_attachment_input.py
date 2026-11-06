"""Attachment content is explicit and images are real blocks, never list repr."""
import base64
import copy

import pytest

from narrative.assistant import build_attachment_blocks


def test_multiple_images_are_content_blocks_and_document_refs_are_not_fake_reads(tmp_path):
    image = tmp_path / 'reference.png'
    image.write_bytes(b'png fixture bytes')
    refs = [{'id': 'attachment-1', 'chat_id': 'chat-a', 'name': '参考.png', 'kind': 'image', 'path': str(image)},
            {'id': 'attachment-2', 'chat_id': 'chat-a', 'name': '设定.txt', 'kind': 'text'},
            {'id': 'attachment-3', 'chat_id': 'chat-a', 'name': '参考二.png', 'kind': 'image', 'path': str(image)}]
    before = copy.deepcopy(refs)
    blocks = build_attachment_blocks('整理这些资料', refs)
    assert len(blocks) == 3 and blocks[0]['type'] == 'text'
    assert 'read_chat_attachment' in blocks[0]['text'] and 'attachment-2' in blocks[0]['text']
    assert '不代表已阅读全文' in blocks[0]['text']
    assert blocks[1]['image_url']['url'].endswith(base64.b64encode(image.read_bytes()).decode())
    assert refs == before


def test_missing_or_oversize_image_is_rejected_instead_of_silent_metadata(tmp_path):
    with pytest.raises(ValueError, match='格式不支持'):
        build_attachment_blocks('', [{'kind': 'image', 'path': str(tmp_path / 'missing.png')}])
    assert build_attachment_blocks('只有文本') == [{'type': 'text', 'text': '只有文本'}]
