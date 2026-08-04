from __future__ import annotations

from pathlib import Path

from docx import Document
from pypdf import PdfReader

from bid_assistant.models import ParsedDocument, ParsedPage


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class DocumentParseError(RuntimeError):
    pass


def _decode_text(path: Path) -> str:
    content = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_pdf(path: Path) -> tuple[list[ParsedPage], list[str]]:
    warnings: list[str] = []
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise DocumentParseError(f"PDF 无法读取：{exc}") from exc

    pages = [
        ParsedPage(page_number=index, text=(page.extract_text() or "").strip())
        for index, page in enumerate(reader.pages, start=1)
    ]
    if reader.is_encrypted:
        warnings.append("PDF 带有加密标记，部分内容可能无法解析。")
    return pages, warnings


def _parse_docx(path: Path) -> list[ParsedPage]:
    try:
        document = Document(str(path))
    except Exception as exc:
        raise DocumentParseError(f"DOCX 无法读取：{exc}") from exc

    blocks: list[str] = []
    blocks.extend(paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip())
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                blocks.append(" | ".join(cells))
    return [ParsedPage(page_number=None, text="\n".join(blocks))]


def parse_document(path: str | Path) -> ParsedDocument:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParseError(f"暂不支持 {suffix or '未知'} 格式。")

    warnings: list[str] = []
    if suffix == ".pdf":
        pages, pdf_warnings = _parse_pdf(source)
        warnings.extend(pdf_warnings)
    elif suffix == ".docx":
        pages = _parse_docx(source)
    else:
        pages = [ParsedPage(page_number=1, text=_decode_text(source).strip())]

    full_text = "\n\n".join(
        f"[第 {page.page_number} 页]\n{page.text}" if page.page_number else page.text
        for page in pages
        if page.text
    ).strip()
    char_count = sum(len(page.text) for page in pages)
    possible_scanned = suffix == ".pdf" and char_count < max(80, len(pages) * 30)
    if possible_scanned:
        warnings.append("页面文本过少，可能是扫描件。请接入 MinerU/RAGFlow OCR 后再分析。")
    if not full_text:
        warnings.append("没有提取到可分析文本。")

    return ParsedDocument(
        filename=source.name,
        file_type=suffix.lstrip("."),
        pages=pages,
        full_text=full_text,
        char_count=char_count,
        possible_scanned_document=possible_scanned,
        warnings=warnings,
    )
