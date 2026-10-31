"""File import and agent-authored source ranges use the public project boundary."""

from __future__ import annotations

import shutil
import zipfile

import pytest

from narrative.graph_operations import GraphOperations
from narrative.project_catalog import create_graph_project


@pytest.fixture()
def ops(tmp_path):
    return create_graph_project(tmp_path / "documents.nproj")


@pytest.mark.parametrize("suffix,encoding", [("txt", "utf-8-sig"), ("md", "utf-8"), ("txt", "gb18030"), ("txt", "utf-16")])
def test_import_preserves_decoded_text_and_original_file(ops, tmp_path, suffix, encoding):
    text = "# 第一章\n欧阳明与海伦见面。\n"
    path = tmp_path / f"chapter.{suffix}"
    content = text.encode(encoding)
    path.write_bytes(content)
    source_id = ops.import_source_file(path)
    assert ops.source(source_id)["text"] == text
    assert path.read_bytes() == content
    assert ops.import_source_file(path) == source_id
    assert ops.source_slices(source_id) == []


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-16-be"])
def test_epub_uses_spine_order_and_excludes_scripts(ops, tmp_path, encoding):
    path = tmp_path / "book.epub"
    bom = {"utf-8": b"", "utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}[encoding]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>')
        archive.writestr("OPS/package.opf", '<package><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
        archive.writestr("OPS/a.xhtml", bom + '<html><body><p>第二段</p><script>hidden</script></body></html>'.encode(encoding))
        archive.writestr("OPS/b.xhtml", bom + '<html><body><h1>第一章</h1><p>先读&amp;这里</p></body></html>'.encode(encoding))
    source_id = ops.import_source_file(path)
    assert ops.source(source_id)["text"] == "第一章\n先读&这里\n\n第二段"


def _pdf(path, *, blank=False, encrypted=False):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if not blank:
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 250 Td (Hello source) Tj ET")
        page[NameObject("/Contents")] = stream
    if encrypted:
        writer.encrypt("secret")
    writer.write(path)


def test_pdf_import_extracts_text(ops, tmp_path):
    path = tmp_path / "text.pdf"
    _pdf(path)
    source_id = ops.import_source_file(path)
    assert "Hello source" in ops.source(source_id)["text"]


@pytest.mark.parametrize("blank,encrypted,message", [(True, False, "OCR"), (False, True, "加密")])
def test_pdf_without_accessible_text_is_not_imported(ops, tmp_path, blank, encrypted, message):
    path = tmp_path / "inaccessible.pdf"
    _pdf(path, blank=blank, encrypted=encrypted)
    with pytest.raises(ValueError, match=message):
        ops.import_source_file(path)
    assert ops.sources() == []


def test_unsupported_empty_and_malformed_files_do_not_import(ops, tmp_path):
    for name, content in [("bad.doc", b"word"), ("empty.txt", b"  "), ("broken.epub", b"bad zip")]:
        path = tmp_path / name
        path.write_bytes(content)
        with pytest.raises(ValueError):
            ops.import_source_file(path)
    assert ops.sources() == []


def test_slice_batches_are_atomic_persisted_and_do_not_change_source(ops):
    source_id = ops.import_source("文件", "甲😀乙\n丙丁")
    revision = ops.graph_project_info()["revision"]
    with pytest.raises(ValueError):
        ops.create_source_slices(source_id, [{"title": "有效", "start": 0, "end": 2},
                                             {"title": "越界", "start": 0, "end": 99}])
    assert ops.source_slices(source_id) == []
    rows = ops.create_source_slices(source_id, [{"title": "第二片", "start": 4, "end": 6},
                                               {"title": "第一片", "start": 0, "end": 3}])
    assert [row["title"] for row in rows] == ["第一片", "第二片"]
    assert rows[0]["length"] == 3
    assert ops.source_slice(source_id, rows[0]["id"])["text"] == "甲😀乙"
    again = ops.create_source_slices(source_id, [{"title": "第一片", "start": 0, "end": 3}])
    assert again == rows
    reopened = GraphOperations(ops.project_info().path)
    assert reopened.source_slices(source_id) == rows
    assert ops.source(source_id)["text"] == "甲😀乙\n丙丁"
    assert ops.graph_project_info()["revision"] == revision
    with pytest.raises(ValueError, match="不存在"):
        ops.source_slice(source_id, "missing")


def test_slices_move_with_project_database_without_entering_graph_history(ops, tmp_path):
    source_id = ops.import_source("原文", "完整的原文")
    rows = ops.create_source_slices(source_id, [{"title": "一片", "start": 0, "end": 2}])
    assert "source_slices" not in str(ops.graph_snapshot())
    package = tmp_path / "copied.nproj"
    shutil.copytree(ops._store.path.parent, package)
    from narrative.project_catalog import open_graph_project

    copied = open_graph_project(package)
    assert copied.source_slices(source_id) == rows
    assert copied.source_slice(source_id, rows[0]["id"])["text"] == "完整"


@pytest.mark.parametrize("slices", [[], [{"title": "x", "start": True, "end": 2}], [{"title": "", "start": 0, "end": 2}], [{"title": "x", "start": 2, "end": 2}], [{"title": "x", "start": -1, "end": 2}]])
def test_invalid_slice_ranges_are_rejected(ops, slices):
    source_id = ops.import_source("文件", "abcd")
    with pytest.raises(ValueError):
        ops.create_source_slices(source_id, slices)
    assert ops.source_slices(source_id) == []
