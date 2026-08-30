from __future__ import annotations

import importlib
import zipfile
from io import BytesIO
from itertools import pairwise
from types import SimpleNamespace

import pytest

from app.ingestion import (
    ChunkType,
    DocumentFormat,
    IngestionError,
    IngestionErrorCode,
    ParsedBlock,
    ParsedDocument,
    chunk_document,
    detect_document_format,
    parse_document,
)


def _zip_bytes(members: dict[str, str | bytes]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _docx_bytes(*, macro: bool = False) -> bytes:
    content_type = (
        "application/vnd.ms-word.document.macroEnabled.main+xml"
        if macro
        else "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document.main+xml"
    )
    members: dict[str, str | bytes] = {
        "[Content_Types].xml": f"""
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Override PartName="/word/document.xml" ContentType="{content_type}"/>
            </Types>
        """,
        "word/document.xml": """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>线性表</w:t></w:r></w:p>
                <w:p><w:r><w:t>顺序表使用连续存储空间。</w:t></w:r></w:p>
                <w:tbl>
                  <w:tr><w:tc><w:p><w:r><w:t>操作</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>复杂度</w:t></w:r></w:p></w:tc></w:tr>
                  <w:tr><w:tc><w:p><w:r><w:t>访问</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>O(1)</w:t></w:r></w:p></w:tc></w:tr>
                </w:tbl>
              </w:body>
            </w:document>
        """,
    }
    if macro:
        members["word/vbaProject.bin"] = b"macro payload is never executed"
    return _zip_bytes(members)


def _pptx_bytes() -> bytes:
    return _zip_bytes(
        {
            "[Content_Types].xml": """
                <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
                  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
                </Types>
            """,
            "ppt/presentation.xml": """
                <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
                </p:presentation>
            """,
            "ppt/_rels/presentation.xml.rels": """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
                  <Relationship Id="rIdExternal" Type="external" Target="https://example.invalid/never-fetched" TargetMode="External"/>
                </Relationships>
            """,
            "ppt/slides/slide1.xml": """
                <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
                  <p:cSld><p:spTree>
                    <p:sp><p:nvSpPr><p:cNvPr id="1" name="Title"/><p:cNvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>虚拟内存</a:t></a:r></a:p></p:txBody></p:sp>
                    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:t>页面置换在页框不足时发生。</a:t></a:r></a:p></p:txBody></p:sp>
                    <p:graphicFrame><a:graphic><a:graphicData><a:tbl>
                      <a:tr><a:tc><a:txBody><a:p><a:r><a:t>算法</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>特点</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
                    </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
                  </p:spTree></p:cSld>
                </p:sld>
            """,
        }
    )


def test_format_validation_uses_content_and_rejects_active_ooxml():
    docx = _docx_bytes()
    assert detect_document_format(docx, "lecture.docx") is DocumentFormat.DOCX

    with pytest.raises(IngestionError) as mismatch:
        detect_document_format(docx, "lecture.pptx")
    assert mismatch.value.code is IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH

    with pytest.raises(IngestionError) as unsafe:
        parse_document(_docx_bytes(macro=True), "lecture.docx")
    assert unsafe.value.code is IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT


def test_docx_and_pptx_preserve_structure_without_resolving_links():
    docx = parse_document(_docx_bytes(), "lecture.docx")
    assert docx.blocks[0].section_path == ("线性表",)
    assert docx.blocks[0].title_level == 1
    assert docx.blocks[1].chunk_type is ChunkType.TABLE

    pptx = parse_document(_pptx_bytes(), "slides.pptx")
    assert [block.page for block in pptx.blocks] == [1, 1]
    assert {block.chunk_type for block in pptx.blocks} == {
        ChunkType.TEXT,
        ChunkType.TABLE,
    }
    assert all(block.section_path == ("虚拟内存",) for block in pptx.blocks)
    assert "example.invalid" not in "".join(block.text for block in pptx.blocks)


def test_pdf_failures_have_stable_codes(monkeypatch):
    with pytest.raises(IngestionError) as damaged:
        parse_document(b"%PDF-1.7\nmissing trailer", "broken.pdf")
    assert damaged.value.code is IngestionErrorCode.DOCUMENT_CORRUPTED

    class EncryptedReader:
        is_encrypted = True

        def __init__(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(PdfReader=EncryptedReader),
    )
    with pytest.raises(IngestionError) as encrypted:
        parse_document(b"%PDF-1.7\ntrailer\n%%EOF", "protected.pdf")
    assert encrypted.value.code is IngestionErrorCode.PDF_PASSWORD_PROTECTED

    class EmptyReader:
        is_encrypted = False

        def __init__(self, *_args, **_kwargs):
            self.pages = [SimpleNamespace(extract_text=lambda: " \n ")]

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: SimpleNamespace(PdfReader=EmptyReader),
    )
    with pytest.raises(IngestionError) as empty:
        parse_document(b"%PDF-1.7\ntrailer\n%%EOF", "scan.pdf")
    assert empty.value.code is IngestionErrorCode.DOCUMENT_EMPTY


def test_markdown_parsing_keeps_heading_context_and_atomic_blocks():
    markdown = """# 栈

栈是只允许在一端进行插入和删除操作的线性表。

| 操作 | 复杂度 |
| --- | --- |
| push | O(1) |

```python
stack.append(item)
```
"""
    parsed = parse_document(markdown.encode(), "stack.md")
    assert [block.chunk_type for block in parsed.blocks] == [
        ChunkType.TEXT,
        ChunkType.TABLE,
        ChunkType.CODE_BLOCK,
    ]
    assert all(block.section_path == ("栈",) for block in parsed.blocks)
    assert all("# 栈" not in block.text for block in parsed.blocks)


def test_markdown_front_matter_is_stripped_not_embedded():
    markdown = """---
title: 数据结构：从接口到不变量
license: CC-BY-4.0
provenance: CoursePilot 原创开放样例
---

# 数据结构

线性表是按线性顺序组织元素的数据结构。
"""
    parsed = parse_document(markdown.encode(), "ds.md")
    assert len(parsed.blocks) == 1
    # Metadata keys and the front-matter block never reach chunks, retrieval,
    # or the deterministic heading candidate extractor.
    assert all("license" not in block.text for block in parsed.blocks)
    assert all("title:" not in block.text for block in parsed.blocks)
    assert parsed.blocks[0].section_path == ("数据结构",)


def test_markdown_front_matter_with_windows_newlines_is_stripped():
    markdown = (
        "---\r\n"
        "title: 操作系统\r\n"
        "license: CC-BY-4.0\r\n"
        "---\r\n\r\n"
        "# 操作系统\r\n\r\n"
        "进程是资源分配与执行状态的载体。\r\n"
    )
    parsed = parse_document(markdown.encode(), "os.md")
    assert len(parsed.blocks) == 1
    assert "title:" not in parsed.blocks[0].text
    assert parsed.blocks[0].section_path == ("操作系统",)


def test_markdown_without_front_matter_is_unchanged():
    markdown = "--- 剧情分隔线并不是前置元数据\n\n正文继续。\n"
    parsed = parse_document(markdown.encode(), "note.md")
    # A "--- " line with trailing text is a horizontal rule / paragraph, not a
    # front-matter fence: nothing is stripped.
    assert len(parsed.blocks) == 2
    assert "剧情分隔线" in parsed.blocks[0].text


def test_chunker_enforces_bounds_structure_and_stable_adjacency():
    section = ("第一章", "线性表")
    document = ParsedDocument(
        document_format=DocumentFormat.TXT,
        blocks=(
            ParsedBlock("甲" * 1300, page=1, section_path=section, title_level=2),
            ParsedBlock(
                "代" * 700,
                chunk_type=ChunkType.CODE_BLOCK,
                page=1,
                section_path=section,
                title_level=2,
            ),
            ParsedBlock(
                "表" * 850,
                chunk_type=ChunkType.TABLE,
                page=1,
                section_path=section,
                title_level=2,
            ),
            ParsedBlock("乙" * 80, page=2, section_path=section, title_level=2),
        ),
    )

    first = chunk_document(document, document_key="version-1")
    second = chunk_document(document, document_key="version-1")
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert max(chunk.char_count for chunk in first) <= 900

    text_page_one = [
        chunk
        for chunk in first
        if chunk.chunk_type is ChunkType.TEXT and chunk.page == 1
    ]
    assert all(300 <= chunk.char_count <= 600 for chunk in text_page_one)
    assert [
        chunk.char_count for chunk in first if chunk.chunk_type is ChunkType.CODE_BLOCK
    ] == [700]
    assert [
        chunk.char_count for chunk in first if chunk.chunk_type is ChunkType.TABLE
    ] == [850]
    assert [chunk.char_count for chunk in first if chunk.page == 2] == [80]
    assert first[0].embedding_text.startswith("第一章 > 线性表\n\n")
    assert "第一章" not in first[0].content

    assert first[0].previous_chunk_id is None
    assert first[-1].next_chunk_id is None
    for previous, current in pairwise(first):
        assert previous.next_chunk_id == current.chunk_id
        assert current.previous_chunk_id == previous.chunk_id
