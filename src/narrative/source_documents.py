"""Read supported source files without changing their contents on disk."""

from __future__ import annotations

import posixpath
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024


def _decode_unicode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _epub_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise ValueError("EPUB 解压内容超过 100 MB")
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB 缺少内容目录")
        package_path = rootfile.attrib["full-path"]
        package = ElementTree.fromstring(archive.read(package_path))
        manifest = {item.attrib["id"]: item for item in package.findall("./{*}manifest/{*}item")}
        chapters = []
        for ref in package.findall("./{*}spine/{*}itemref"):
            item = manifest.get(ref.get("idref", ""))
            if item is None:
                raise ValueError("EPUB 阅读顺序引用了不存在的章节")
            if ref.get("linear", "yes") == "no":
                continue
            if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            href = unquote(item.get("href", "").split("#", 1)[0])
            member = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), href))
            parser = _Text()
            parser.feed(_decode_unicode(archive.read(member)))
            text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
            if text:
                chapters.append(text)
        return "\n\n".join(chapters)


def read_source_file(file_path: str | Path) -> str:
    path = Path(file_path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("原文文件不存在或超过 50 MB")
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            raw = path.read_bytes()
            # BOMs are explicit; legacy Chinese text is otherwise decoded strictly.
            try:
                text = _decode_unicode(raw)
            except UnicodeDecodeError:
                if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                    raise
                text = raw.decode("gb18030")
        elif suffix == ".epub":
            text = _epub_text(path)
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise ValueError("PDF 导入需要 pypdf，请安装项目依赖") from exc
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("PDF 已加密，请先解密后导入")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                raise ValueError("PDF 没有可提取文字，扫描件需先进行 OCR")
        else:
            raise ValueError("仅支持 TXT、MD、PDF、EPUB 文件")
    except (OSError, UnicodeError, zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ValueError(f"原文文件无法读取：{exc}") from exc
    if not text.strip():
        raise ValueError("文件没有可导入的文字")
    return text
