from __future__ import annotations

import importlib
import posixpath
import re
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from app.ingestion.errors import (
    IngestionError,
    IngestionErrorCode,
    ingestion_error,
)
from app.ingestion.types import (
    ChunkType,
    DocumentFormat,
    ParsedBlock,
    ParsedDocument,
)

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_XML_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_XML_COMPRESSION_RATIO = 500

_EXTENSIONS: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".docx": DocumentFormat.DOCX,
    ".pptx": DocumentFormat.PPTX,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TXT,
}
_PDF_HEADER_RE = re.compile(rb"%PDF-[12]\.[0-9]")
_PDF_HEADER_PREFIXES = (b"\xef\xbb\xbf", b"\r", b"\n", b"\t", b" ")
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEADING_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+){0,5})[\s、.]+\S")
_CHAPTER_HEADING_RE = re.compile(r"^第\s*[^\s]{1,12}\s*(章|篇)\s*\S*")
_SECTION_HEADING_RE = re.compile(r"^第\s*[^\s]{1,12}\s*(节)\s*\S*")
_LIST_RE = re.compile(
    r"^\s*(?:[-*+•▪◦]|\d+[.)、]|[（(][一二三四五六七八九十\d]+[)）])\s+"
)
_EXAMPLE_RE = re.compile(r"^(?:例(?:题)?\s*\d*|示例|example\b)", re.IGNORECASE)
_ANSWER_RE = re.compile(r"^(?:答案|解答|解析|answer\b)", re.IGNORECASE)
_EXPERIMENT_RE = re.compile(
    r"^(?:实验步骤|操作步骤|步骤\s*\d+|step\s*\d+)", re.IGNORECASE
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"

_DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_PPTX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)


def detect_document_format(data: bytes, filename: str) -> DocumentFormat:
    """Validate the declared extension against magic bytes or OOXML members."""

    extension = Path(filename).suffix.casefold()
    expected = _EXTENSIONS.get(extension)
    if expected is None:
        raise ingestion_error(
            IngestionErrorCode.UNSUPPORTED_DOCUMENT_FORMAT,
            extension=extension or None,
        )
    if not data:
        raise ingestion_error(IngestionErrorCode.DOCUMENT_EMPTY)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_TOO_LARGE,
            max_bytes=MAX_DOCUMENT_BYTES,
        )

    if expected is DocumentFormat.PDF:
        if _starts_with_zip_signature(data) or _pdf_header(data) is None:
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
                expected=expected.value,
            )
        _validate_pdf_envelope(data)
        return expected

    if expected in {DocumentFormat.DOCX, DocumentFormat.PPTX}:
        if not _starts_with_zip_signature(data):
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
                expected=expected.value,
            )
        actual = _inspect_ooxml_format(data)
        if actual is not expected:
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
                expected=expected.value,
                detected=actual.value if actual else None,
            )
        return expected

    if _pdf_header(data) is not None or _starts_with_zip_signature(data):
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
            expected=expected.value,
        )
    _decode_text_document(data)
    return expected


def parse_document(data: bytes, filename: str) -> ParsedDocument:
    """Parse supported document bytes without executing active or linked content."""

    document_format = detect_document_format(data, filename)
    if document_format is DocumentFormat.PDF:
        blocks = _parse_pdf(data)
    elif document_format is DocumentFormat.DOCX:
        blocks = _parse_docx(data)
    elif document_format is DocumentFormat.PPTX:
        blocks = _parse_pptx(data)
    elif document_format is DocumentFormat.MARKDOWN:
        blocks, _ = _parse_line_oriented(_decode_text_document(data), markdown=True)
    else:
        blocks, _ = _parse_line_oriented(_decode_text_document(data))

    visible_blocks = tuple(block for block in blocks if _has_visible_text(block.text))
    if not visible_blocks:
        raise ingestion_error(IngestionErrorCode.DOCUMENT_EMPTY)
    return ParsedDocument(document_format=document_format, blocks=visible_blocks)


def _starts_with_zip_signature(data: bytes) -> bool:
    return data.startswith(_ZIP_SIGNATURES)


def _pdf_header(data: bytes) -> re.Match[bytes] | None:
    prefix = data[:1024]
    for removable in _PDF_HEADER_PREFIXES:
        if prefix.startswith(removable):
            prefix = prefix.lstrip(b"\xef\xbb\xbf\r\n\t ")
            break
    return _PDF_HEADER_RE.match(prefix)


def _validate_pdf_envelope(data: bytes) -> None:
    if _pdf_header(data) is None or b"%%EOF" not in data[-2048:]:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            document_format=DocumentFormat.PDF.value,
        )


def _decode_text_document(data: bytes) -> str:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
            expected="UTF-8 or BOM-marked UTF-16 text",
        ) from exc

    control_count = len(_CONTROL_RE.findall(text))
    if "\x00" in text or control_count > max(2, len(text) // 100):
        raise ingestion_error(IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH)
    if not _has_visible_text(text):
        raise ingestion_error(IngestionErrorCode.DOCUMENT_EMPTY)
    return text


def _open_validated_package(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError) as exc:
        raise ingestion_error(IngestionErrorCode.DOCUMENT_CORRUPTED) from exc

    try:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
                reason="too_many_archive_members",
            )

        names: set[str] = set()
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            if (
                not name
                or "\x00" in name
                or "\\" in name
                or path.is_absolute()
                or ".." in path.parts
                or name in names
            ):
                raise ingestion_error(
                    IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
                    reason="unsafe_archive_member",
                )
            if info.flag_bits & 0x1:
                raise ingestion_error(
                    IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
                    reason="encrypted_archive_member",
                )
            names.add(name)
    except Exception:
        archive.close()
        raise
    return archive


def _read_package_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            missing_part=name,
        ) from exc

    compressed_size = max(info.compress_size, 1)
    if (
        info.file_size > MAX_XML_MEMBER_BYTES
        or info.file_size / compressed_size > MAX_XML_COMPRESSION_RATIO
    ):
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
            reason="oversized_xml_part",
            part=name,
        )
    try:
        return archive.read(info)
    except (RuntimeError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            part=name,
        ) from exc


def _parse_xml(raw: bytes, part: str) -> ElementTree.Element:
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
            reason="xml_entity_declaration",
            part=part,
        )
    try:
        return ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError) as exc:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            part=part,
        ) from exc


def _inspect_ooxml_format(data: bytes) -> DocumentFormat | None:
    archive = _open_validated_package(data)
    try:
        names = set(archive.namelist())
        has_docx_main = "word/document.xml" in names
        has_pptx_main = "ppt/presentation.xml" in names
        if not has_docx_main and not has_pptx_main:
            return None
        if has_docx_main and has_pptx_main:
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_CORRUPTED,
                reason="ambiguous_ooxml_package",
            )

        lowered_names = {name.casefold() for name in names}
        if any(name.endswith("vbaproject.bin") for name in lowered_names):
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT,
                reason="macro_payload",
            )

        content_types_raw = _read_package_member(archive, "[Content_Types].xml")
        content_types = _parse_xml(content_types_raw, "[Content_Types].xml")
        overrides = {
            element.attrib.get("PartName"): element.attrib.get("ContentType", "")
            for element in content_types
            if _local_name(element.tag) == "Override"
        }
        if has_docx_main:
            content_type = overrides.get("/word/document.xml", "")
            if (
                "macroEnabled" in content_type
                or content_type != _DOCX_MAIN_CONTENT_TYPE
            ):
                code = (
                    IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT
                    if "macroEnabled" in content_type
                    else IngestionErrorCode.DOCUMENT_CORRUPTED
                )
                raise ingestion_error(code, part="word/document.xml")
            return DocumentFormat.DOCX

        content_type = overrides.get("/ppt/presentation.xml", "")
        if "macroEnabled" in content_type or content_type != _PPTX_MAIN_CONTENT_TYPE:
            code = (
                IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT
                if "macroEnabled" in content_type
                else IngestionErrorCode.DOCUMENT_CORRUPTED
            )
            raise ingestion_error(code, part="ppt/presentation.xml")
        return DocumentFormat.PPTX
    finally:
        archive.close()


def _parse_pdf(data: bytes) -> list[ParsedBlock]:
    try:
        pypdf = importlib.import_module("pypdf")
    except ImportError as exc:
        raise ingestion_error(
            IngestionErrorCode.PARSER_DEPENDENCY_MISSING,
            dependency="pypdf",
        ) from exc

    try:
        reader = pypdf.PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ingestion_error(IngestionErrorCode.PDF_PASSWORD_PROTECTED)

        blocks: list[ParsedBlock] = []
        section_path: tuple[str, ...] = ()
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            page_blocks, section_path = _parse_line_oriented(
                text,
                page=page_number,
                section_path=section_path,
            )
            blocks.extend(page_blocks)
        return blocks
    except IngestionError:
        raise
    except Exception as exc:
        if _looks_like_pdf_password_error(exc):
            raise ingestion_error(IngestionErrorCode.PDF_PASSWORD_PROTECTED) from exc
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            document_format=DocumentFormat.PDF.value,
        ) from exc


def _looks_like_pdf_password_error(exc: Exception) -> bool:
    return type(exc).__name__ in {
        "FileNotDecryptedError",
        "PasswordRequiredError",
        "WrongPasswordError",
    }


def _parse_docx(data: bytes) -> list[ParsedBlock]:
    archive = _open_validated_package(data)
    try:
        raw = _read_package_member(archive, "word/document.xml")
        root = _parse_xml(raw, "word/document.xml")
    finally:
        archive.close()

    body = root.find(f".//{{{_W}}}body")
    if body is None:
        raise ingestion_error(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            part="word/document.xml",
        )

    blocks: list[ParsedBlock] = []
    section_path: tuple[str, ...] = ()
    title_level: int | None = None
    for element in body:
        name = _local_name(element.tag)
        if name == "p":
            text = _word_paragraph_text(element)
            if not _has_visible_text(text):
                continue
            heading_level = _docx_heading_level(element, text)
            if heading_level is not None:
                section_path = _set_section(section_path, heading_level, text.strip())
                title_level = heading_level
                continue

            style = element.find(f"./{{{_W}}}pPr/{{{_W}}}pStyle")
            style_name = (
                style.attrib.get(f"{{{_W}}}val", "") if style is not None else ""
            )
            if element.find(f"./{{{_W}}}pPr/{{{_W}}}numPr") is not None:
                chunk_type = ChunkType.LIST
            elif element.find(f".//{{{_M}}}oMath") is not None:
                chunk_type = ChunkType.FORMULA
            elif re.search(r"code|preformatted|source", style_name, re.IGNORECASE):
                chunk_type = ChunkType.CODE
            else:
                chunk_type = _classify_text(text)
            blocks.append(
                ParsedBlock(
                    text=text.strip(),
                    chunk_type=chunk_type,
                    section_path=section_path,
                    title_level=title_level,
                )
            )
        elif name == "tbl":
            table_text = _word_table_text(element)
            if _has_visible_text(table_text):
                blocks.append(
                    ParsedBlock(
                        text=table_text,
                        chunk_type=ChunkType.TABLE,
                        section_path=section_path,
                        title_level=title_level,
                    )
                )
    return blocks


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        name = _local_name(element.tag)
        if name == "t" and element.text:
            parts.append(element.text)
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _word_table_text(table: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"./{{{_W}}}tr"):
        cells: list[str] = []
        for cell in row.findall(f"./{{{_W}}}tc"):
            paragraphs = [
                _word_paragraph_text(paragraph)
                for paragraph in cell.findall(f".//{{{_W}}}p")
            ]
            cells.append(" ".join(text for text in paragraphs if text).strip())
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows).strip()


def _docx_heading_level(paragraph: ElementTree.Element, text: str) -> int | None:
    style = paragraph.find(f"./{{{_W}}}pPr/{{{_W}}}pStyle")
    style_name = style.attrib.get(f"{{{_W}}}val", "") if style is not None else ""
    match = re.search(r"(?:heading|标题)[\s_-]*(\d+)", style_name, re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 6))
    outline = paragraph.find(f"./{{{_W}}}pPr/{{{_W}}}outlineLvl")
    if outline is not None:
        value = outline.attrib.get(f"{{{_W}}}val")
        if value is not None and value.isdigit():
            return max(1, min(int(value) + 1, 6))
    return _plain_heading_level(text)


def _parse_pptx(data: bytes) -> list[ParsedBlock]:
    archive = _open_validated_package(data)
    try:
        slide_names = _ordered_slide_names(archive)
        blocks: list[ParsedBlock] = []
        for page, slide_name in enumerate(slide_names, start=1):
            slide = _parse_xml(_read_package_member(archive, slide_name), slide_name)
            blocks.extend(_parse_slide(slide, page))
        return blocks
    finally:
        archive.close()


def _ordered_slide_names(archive: zipfile.ZipFile) -> list[str]:
    presentation_name = "ppt/presentation.xml"
    relationships_name = "ppt/_rels/presentation.xml.rels"
    presentation = _parse_xml(
        _read_package_member(archive, presentation_name), presentation_name
    )
    relationships = _parse_xml(
        _read_package_member(archive, relationships_name), relationships_name
    )

    relationship_targets: dict[str, str] = {}
    for relationship in relationships.findall(f".//{{{_PKG_R}}}Relationship"):
        if relationship.attrib.get("TargetMode", "Internal") == "External":
            continue
        relation_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if not relation_id or not target:
            continue
        normalized = posixpath.normpath(posixpath.join("ppt", target.lstrip("/")))
        if normalized.startswith("ppt/slides/"):
            relationship_targets[relation_id] = normalized

    names = set(archive.namelist())
    slide_names: list[str] = []
    for slide_id in presentation.findall(f".//{{{_P}}}sldId"):
        relation_id = slide_id.attrib.get(f"{{{_R}}}id")
        target = relationship_targets.get(relation_id or "")
        if target is None or target not in names:
            raise ingestion_error(
                IngestionErrorCode.DOCUMENT_CORRUPTED,
                part=presentation_name,
            )
        slide_names.append(target)
    return slide_names


def _parse_slide(slide: ElementTree.Element, page: int) -> list[ParsedBlock]:
    shape_tree = slide.find(f".//{{{_P}}}spTree")
    if shape_tree is None:
        return []

    title = ""
    for shape in shape_tree.findall(f".//{{{_P}}}sp"):
        placeholder = shape.find(f"./{{{_P}}}nvSpPr/{{{_P}}}nvPr/{{{_P}}}ph")
        placeholder_type = (
            placeholder.attrib.get("type", "") if placeholder is not None else ""
        )
        if placeholder_type in {"title", "ctrTitle"}:
            title = _drawing_text(shape)
            if title:
                break
    section_path = (title.strip(),) if title.strip() else ()
    title_level = 1 if section_path else None

    blocks: list[ParsedBlock] = []
    for child in shape_tree:
        name = _local_name(child.tag)
        if name == "sp":
            placeholder = child.find(f"./{{{_P}}}nvSpPr/{{{_P}}}nvPr/{{{_P}}}ph")
            placeholder_type = (
                placeholder.attrib.get("type", "") if placeholder is not None else ""
            )
            if placeholder_type in {"title", "ctrTitle"}:
                continue
            for paragraph in child.findall(f".//{{{_A}}}p"):
                text = "".join(
                    item.text or "" for item in paragraph.findall(f".//{{{_A}}}t")
                ).strip()
                if not text:
                    continue
                paragraph_properties = paragraph.find(f"./{{{_A}}}pPr")
                has_bullet = paragraph_properties is not None and (
                    paragraph_properties.attrib.get("lvl") is not None
                    or any(
                        _local_name(item.tag).startswith("bu")
                        for item in paragraph_properties
                    )
                )
                chunk_type = ChunkType.LIST if has_bullet else _classify_text(text)
                blocks.append(
                    ParsedBlock(
                        text=text,
                        chunk_type=chunk_type,
                        page=page,
                        section_path=section_path,
                        title_level=title_level,
                    )
                )
        elif name == "graphicFrame":
            table = child.find(f".//{{{_A}}}tbl")
            if table is None:
                continue
            table_text = _drawing_table_text(table)
            if table_text:
                blocks.append(
                    ParsedBlock(
                        text=table_text,
                        chunk_type=ChunkType.TABLE,
                        page=page,
                        section_path=section_path,
                        title_level=title_level,
                    )
                )
    return blocks


def _drawing_text(element: ElementTree.Element) -> str:
    paragraphs: list[str] = []
    for paragraph in element.findall(f".//{{{_A}}}p"):
        text = "".join(
            item.text or "" for item in paragraph.findall(f".//{{{_A}}}t")
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _drawing_table_text(table: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in table.findall(f"./{{{_A}}}tr"):
        cells = [
            _drawing_text(cell).replace("\n", " ").strip()
            for cell in row.findall(f"./{{{_A}}}tc")
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows).strip()


def _parse_line_oriented(
    text: str,
    *,
    markdown: bool = False,
    page: int | None = None,
    section_path: tuple[str, ...] = (),
) -> tuple[list[ParsedBlock], tuple[str, ...]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[ParsedBlock] = []
    title_level: int | None = len(section_path) or None
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            fence = fence_match.group(1)
            code_lines = [line]
            index += 1
            while index < len(lines):
                code_lines.append(lines[index].rstrip())
                if re.match(
                    rf"^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$", lines[index]
                ):
                    index += 1
                    break
                index += 1
            blocks.append(
                ParsedBlock(
                    text="\n".join(code_lines).strip(),
                    chunk_type=ChunkType.CODE,
                    page=page,
                    section_path=section_path,
                    title_level=title_level,
                )
            )
            continue

        heading_level = (
            _markdown_heading_level(stripped)
            if markdown
            else _plain_heading_level(stripped)
        )
        if heading_level is not None:
            heading = re.sub(r"^#{1,6}\s+", "", stripped).strip().rstrip("#").strip()
            section_path = _set_section(section_path, heading_level, heading)
            title_level = heading_level
            index += 1
            continue

        if _is_table_start(lines, index, markdown=markdown):
            table_lines: list[str] = []
            while index < len(lines) and _looks_like_table_line(lines[index]):
                table_lines.append(lines[index].strip())
                index += 1
            blocks.append(
                ParsedBlock(
                    text="\n".join(table_lines),
                    chunk_type=ChunkType.TABLE,
                    page=page,
                    section_path=section_path,
                    title_level=title_level,
                )
            )
            continue

        if _LIST_RE.match(stripped):
            list_lines: list[str] = []
            while index < len(lines):
                candidate = lines[index].rstrip()
                if _LIST_RE.match(candidate.strip()) or (
                    candidate.startswith(("  ", "\t")) and candidate.strip()
                ):
                    list_lines.append(candidate.strip())
                    index += 1
                else:
                    break
            blocks.append(
                ParsedBlock(
                    text="\n".join(list_lines),
                    chunk_type=ChunkType.LIST,
                    page=page,
                    section_path=section_path,
                    title_level=title_level,
                )
            )
            continue

        paragraph_lines = [stripped]
        index += 1
        while index < len(lines):
            candidate = lines[index].rstrip()
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if re.match(r"^\s*(`{3,}|~{3,})", candidate):
                break
            next_heading = (
                _markdown_heading_level(candidate_stripped)
                if markdown
                else _plain_heading_level(candidate_stripped)
            )
            if next_heading is not None or _LIST_RE.match(candidate_stripped):
                break
            if _is_table_start(lines, index, markdown=markdown):
                break
            paragraph_lines.append(candidate_stripped)
            index += 1
        paragraph = "\n".join(paragraph_lines).strip()
        blocks.append(
            ParsedBlock(
                text=paragraph,
                chunk_type=_classify_text(paragraph),
                page=page,
                section_path=section_path,
                title_level=title_level,
            )
        )
    return blocks, section_path


def _markdown_heading_level(text: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+\S", text)
    return len(match.group(1)) if match else None


def _plain_heading_level(text: str) -> int | None:
    if len(text) > 100 or "\n" in text:
        return None
    if _CHAPTER_HEADING_RE.match(text):
        return 1
    if _SECTION_HEADING_RE.match(text):
        return 2
    match = _HEADING_NUMBER_RE.match(text)
    if match:
        return min(match.group(1).count(".") + 1, 6)
    return None


def _set_section(current: tuple[str, ...], level: int, heading: str) -> tuple[str, ...]:
    heading = " ".join(heading.split())
    if not heading:
        return current
    keep = min(level - 1, len(current))
    return (*current[:keep], heading)


def _is_table_start(lines: list[str], index: int, *, markdown: bool) -> bool:
    if not _looks_like_table_line(lines[index]):
        return False
    if not markdown:
        return True
    if index + 1 >= len(lines):
        return False
    separator = lines[index + 1].strip().strip("|")
    cells = [cell.strip() for cell in separator.split("|")]
    return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _looks_like_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2 or stripped.count("\t") >= 2


def _classify_text(text: str) -> ChunkType:
    stripped = text.lstrip()
    if _EXAMPLE_RE.match(stripped):
        return ChunkType.EXAMPLE
    if _ANSWER_RE.match(stripped):
        return ChunkType.ANSWER
    if _EXPERIMENT_RE.match(stripped):
        return ChunkType.EXPERIMENT_STEP
    if stripped.startswith(("$$", "\\[", "\\begin{equation}")):
        return ChunkType.FORMULA
    if _LIST_RE.match(stripped):
        return ChunkType.LIST
    return ChunkType.BODY


def _has_visible_text(text: str) -> bool:
    return any(not character.isspace() for character in text)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
