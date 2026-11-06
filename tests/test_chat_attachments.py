"""User-selected originals stay untouched; imported files are project-owned references."""
import hashlib
from pathlib import Path

import pytest
from docx import Document
from PIL import Image
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from narrative.assistant import build_attachment_blocks
from narrative.chat_store import ChatStore
from narrative.operations import Operations
from narrative.project_package import layout
from narrative.tool_api import call


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setenv('NARRATIVE_PROVIDER_DB', str(tmp_path / 'providers.sqlite3'))
    ops = Operations.create_graph_project(tmp_path / 'attachments.nproj')
    store = ChatStore(ops.project_info().path)
    return ops, store.create()['id']


def test_multiple_unique_copies_text_pages_and_real_image_mime(chat, tmp_path):
    ops, chat_id = chat
    source = tmp_path / 'original.txt'
    source.write_text('甲😀乙\r\n第二行', encoding='utf-8', newline='')
    disguised = tmp_path / 'picture.txt'
    Image.new('RGB', (20, 10), 'red').save(disguised, format='PNG')
    originals = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (source, disguised)}
    graph = ops.graph_snapshot()
    refs = ops.import_chat_attachments(chat_id, [source.as_uri(), str(disguised)])['attachments']
    assert [item['id'] for item in refs] == ['attachment-1', 'attachment-2']
    assert all(Path(item['path']) not in originals for item in refs)
    assert refs[1]['mime_type'] == 'image/png' and Path(refs[1]['image_path']).suffix == '.png'
    assert len(build_attachment_blocks('查看附件', refs)) == 2
    text = ops.read_chat_attachment(chat_id, refs[0]['id'], 1, 2)
    assert text['content'] == '😀乙' and text['next_offset'] == 3 and not text['eof']
    again = ops.import_chat_attachments(chat_id, [str(source)])['attachments'][0]
    assert again['id'] == 'attachment-3' and again['path'] != refs[0]['path']
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in originals.items())
    assert ops.graph_snapshot() == graph
    assert len(call('list_chat_attachments', {'chat_id': chat_id}, project=tmp_path / 'attachments.nproj')['attachments']) == 3


def test_pdf_docx_text_and_scan_unknown_binary_are_honest(chat, tmp_path):
    ops, chat_id = chat
    pdf = tmp_path / 'note.pdf'
    page = canvas.Canvas(str(pdf))
    page.drawString(50, 750, 'Window note for the actor')
    page.save()
    scan = tmp_path / 'scan.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(str(scan))
    word = tmp_path / 'note.docx'
    doc = Document()
    doc.add_paragraph('Turn toward the window.')
    doc.save(word)
    binary = tmp_path / 'unknown.bin'
    binary.write_bytes(b'\x00\xff\x01\x02')
    refs = ops.import_chat_attachments(chat_id, [str(path) for path in (pdf, word, scan, binary)])['attachments']
    assert 'Window note' in ops.read_chat_attachment(chat_id, refs[0]['id'])['content']
    assert 'Turn toward' in ops.read_chat_attachment(chat_id, refs[1]['id'])['content']
    assert refs[0]['text_extraction_scope'] == 'text_only_not_layout_or_images'
    assert refs[2]['text_status'] == 'no_extractable_text'
    for ref in refs[2:]:
        result = ops.read_chat_attachment(chat_id, ref['id'])
        assert not result['parsed'] and result['content'] is None


def test_attachment_batch_failure_rolls_back_only_owned_copies(chat, tmp_path, monkeypatch):
    from narrative import chat_attachments
    ops, chat_id = chat
    one, two = tmp_path / 'one.txt', tmp_path / 'two.txt'
    one.write_text('first')
    two.write_text('second')
    copy = chat_attachments.shutil.copyfileobj
    count = 0
    def fail_second(source, destination):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('simulated copy failure')
        return copy(source, destination)
    monkeypatch.setattr(chat_attachments.shutil, 'copyfileobj', fail_second)
    with pytest.raises(OSError):
        ops.import_chat_attachments(chat_id, [str(one), str(two)])
    assert ops.list_chat_attachments(chat_id)['attachments'] == []
    assert one.read_text() == 'first' and two.read_text() == 'second'
    assert not any(path.is_file() for path in (layout(ops.project_info().path).material / 'chat_attachments').rglob('*'))


def test_chat_scope_and_unicode_sample_boundary(chat, tmp_path):
    ops, chat_id = chat
    path = tmp_path / 'long.txt'
    path.write_text('a'*8191+'汉尾', encoding='utf-8')
    ref = ops.import_chat_attachments(chat_id, [str(path)])['attachments'][0]
    assert ref['text_status'] == 'available'
    assert ops.read_chat_attachment(chat_id, ref['id'], 8191)['content'] == '汉尾'
    other = ChatStore(ops.project_info().path).create()['id']
    assert not ops.list_chat_attachments(other)['attachments']
    with pytest.raises(ValueError, match='指定聊天'):
        ops.read_chat_attachment(other, ref['id'])
