from pathlib import Path

from docx import Document
from pypdf import PdfWriter

from bid_assistant.parsers import parse_document


def test_parse_utf8_text(tmp_path: Path) -> None:
    source = tmp_path / "tender.txt"
    source.write_text("项目名称：测试项目\n投标人必须提交材料。", encoding="utf-8")

    parsed = parse_document(source)

    assert parsed.file_type == "txt"
    assert parsed.char_count > 10
    assert "测试项目" in parsed.full_text
    assert parsed.pages[0].page_number == 1


def test_parse_gb18030_text(tmp_path: Path) -> None:
    source = tmp_path / "legacy.txt"
    source.write_bytes("采购人：测试单位".encode("gb18030"))

    parsed = parse_document(source)

    assert "测试单位" in parsed.full_text


def test_parse_docx_paragraphs_and_tables(tmp_path: Path) -> None:
    source = tmp_path / "tender.docx"
    document = Document()
    document.add_paragraph("项目名称：DOCX 测试项目")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "评分项"
    table.cell(0, 1).text = "10 分"
    document.save(source)

    parsed = parse_document(source)

    assert "DOCX 测试项目" in parsed.full_text
    assert "评分项 | 10 分" in parsed.full_text


def test_blank_pdf_is_marked_as_possible_scan(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with source.open("wb") as stream:
        writer.write(stream)

    parsed = parse_document(source)

    assert parsed.possible_scanned_document is True
    assert any("扫描件" in warning for warning in parsed.warnings)
