from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image

from bid_assistant.models import ChapterDraft, ReviewReport, TenderAnalysis


BODY_FONT = "Times New Roman"
BODY_FONT_EAST_ASIA = "宋体"
HEADING_FONT = "Microsoft YaHei"
HEADING_FONT_EAST_ASIA = "微软雅黑"
CONTENT_WIDTH_DXA = 8901
TABLE_INDENT_DXA = 120
NAVY = "1F4E78"
BLUE = "2F75B5"
DARK_TEXT = "222222"
MUTED_TEXT = "667085"
LIGHT_BLUE = "EAF1F8"
LIGHT_GRAY = "F7F9FC"
BORDER = "B8C6D5"
RISK_RED = "B42318"
LIGHT_RED = "FFF1F0"


def _set_character_spacing(element, points: float) -> None:
    r_pr = element.get_or_add_rPr()
    for existing in r_pr.findall(qn("w:spacing")):
        r_pr.remove(existing)
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:val"), str(round(points * 20)))
    r_pr.append(spacing)


def _set_style_font(
    style,
    *,
    latin: str,
    east_asia: str,
    size: float,
    bold: bool = False,
    color: str = DARK_TEXT,
    character_spacing: float = 0,
) -> None:
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor.from_string(color)
    r_pr = style._element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    for key, value in (("ascii", latin), ("hAnsi", latin), ("cs", latin), ("eastAsia", east_asia)):
        r_fonts.set(qn(f"w:{key}"), value)
    _set_character_spacing(style._element, character_spacing)


def _format_run(
    run,
    *,
    latin: str = BODY_FONT,
    east_asia: str = BODY_FONT_EAST_ASIA,
    size: float | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
    color: str | None = None,
    character_spacing: float = 0,
) -> None:
    run.font.name = latin
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.find(qn("w:rFonts"))
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.insert(0, r_fonts)
    for key, value in (("ascii", latin), ("hAnsi", latin), ("cs", latin), ("eastAsia", east_asia)):
        r_fonts.set(qn(f"w:{key}"), value)
    _set_character_spacing(run._element, character_spacing)


def _set_paragraph_shading(paragraph, fill: str) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    for existing in p_pr.findall(qn("w:shd")):
        p_pr.remove(existing)
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    p_pr.append(shading)


def _set_paragraph_border(paragraph, *, side: str, color: str, size: int = 12, space: int = 8) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    border = p_bdr.find(qn(f"w:{side}"))
    if border is None:
        border = OxmlElement(f"w:{side}")
        p_bdr.append(border)
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), str(size))
    border.set(qn("w:space"), str(space))
    border.set(qn("w:color"), color)


def _configure_document(document: Document) -> None:
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.8)
    section.right_margin = Cm(2.5)
    section.header_distance = Cm(1.25)
    section.footer_distance = Cm(1.25)

    normal = document.styles["Normal"]
    _set_style_font(
        normal,
        latin=BODY_FONT,
        east_asia=BODY_FONT_EAST_ASIA,
        size=10.5,
        color=DARK_TEXT,
    )
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.first_line_indent = Cm(0.74)
    normal.paragraph_format.widow_control = True

    style_tokens = (
        ("Title", 26, NAVY, 0, 10, 2.0),
        ("Subtitle", 15, MUTED_TEXT, 0, 14, 0.8),
        ("Heading 1", 17, NAVY, 16, 8, 0.8),
        ("Heading 2", 14, BLUE, 12, 6, 0.4),
        ("Heading 3", 12, DARK_TEXT, 9, 4, 0.2),
    )
    for name, size, color, before, after, character_spacing in style_tokens:
        style = document.styles[name]
        _set_style_font(
            style,
            latin=HEADING_FONT,
            east_asia=HEADING_FONT_EAST_ASIA,
            size=size,
            bold=True,
            color=color,
            character_spacing=character_spacing,
        )
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.line_spacing = 1.2
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.widow_control = True

    for name in ("List Bullet", "List Number"):
        style = document.styles[name]
        _set_style_font(
            style,
            latin=BODY_FONT,
            east_asia=BODY_FONT_EAST_ASIA,
            size=10.5,
            color=DARK_TEXT,
        )
        style.paragraph_format.space_before = Pt(0)
        style.paragraph_format.space_after = Pt(4)
        style.paragraph_format.line_spacing = 1.25
        style.paragraph_format.first_line_indent = None
        style.paragraph_format.widow_control = True

    settings = document.settings._element
    if settings.find(qn("w:updateFields")) is None:
        update_fields = OxmlElement("w:updateFields")
        update_fields.set(qn("w:val"), "true")
        settings.append(update_fields)


def _create_numbering(document: Document, *, ordered: bool) -> int:
    numbering = document.part.numbering_part.element
    abstract_ids = [
        int(element.get(qn("w:abstractNumId")))
        for element in numbering.findall(qn("w:abstractNum"))
    ]
    num_ids = [int(element.get(qn("w:numId"))) for element in numbering.findall(qn("w:num"))]
    abstract_id = max(abstract_ids, default=0) + 1
    num_id = max(num_ids, default=0) + 1

    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "singleLevel")
    abstract.append(multi_level)

    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    level.append(start)
    num_format = OxmlElement("w:numFmt")
    num_format.set(qn("w:val"), "decimal" if ordered else "bullet")
    level.append(num_format)
    level_text = OxmlElement("w:lvlText")
    level_text.set(qn("w:val"), "%1." if ordered else "•")
    level.append(level_text)
    suffix = OxmlElement("w:suff")
    suffix.set(qn("w:val"), "tab")
    level.append(suffix)
    justification = OxmlElement("w:lvlJc")
    justification.set(qn("w:val"), "left")
    level.append(justification)

    p_pr = OxmlElement("w:pPr")
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "num")
    tab.set(qn("w:pos"), "600")
    tabs.append(tab)
    p_pr.append(tabs)
    indent = OxmlElement("w:ind")
    indent.set(qn("w:left"), "600")
    indent.set(qn("w:hanging"), "300")
    p_pr.append(indent)
    level.append(p_pr)
    abstract.append(level)
    numbering.append(abstract)

    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abstract_ref = OxmlElement("w:abstractNumId")
    abstract_ref.set(qn("w:val"), str(abstract_id))
    num.append(abstract_ref)
    numbering.append(num)
    return num_id


def _apply_numbering(paragraph, num_id: int) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    existing = p_pr.find(qn("w:numPr"))
    if existing is not None:
        p_pr.remove(existing)
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    num = OxmlElement("w:numId")
    num.set(qn("w:val"), str(num_id))
    num_pr.extend([level, num])
    p_pr.append(num_pr)


def _add_page_number(paragraph) -> None:
    paragraph.paragraph_format.first_line_indent = None
    prefix = paragraph.add_run("第 ")
    _format_run(prefix, size=9, color=MUTED_TEXT)
    run = paragraph.add_run()
    _format_run(run, size=9, color=MUTED_TEXT)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    value = OxmlElement("w:t")
    value.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, value, end])
    suffix = paragraph.add_run(" 页")
    _format_run(suffix, size=9, color=MUTED_TEXT)


def _add_header_footer(document: Document, project_name: str) -> None:
    for section in document.sections:
        section.different_first_page_header_footer = True
        first_header = section.first_page_header
        first_header.paragraphs[0].text = ""
        first_footer = section.first_page_footer
        first_footer.paragraphs[0].text = ""

        header = section.header
        header_table = header.add_table(rows=1, cols=2, width=Cm(15.7))
        _set_table_geometry(header_table, [6800, 2101], indent=0)
        _remove_table_borders(header_table)
        _set_table_border_side(header_table, side="bottom", color="D8E0EA", size=4)
        left_header, right_header = header_table.rows[0].cells
        for cell in (left_header, right_header):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell, top=0, bottom=55, start=0, end=0)

        left_paragraph = left_header.paragraphs[0]
        left_paragraph.paragraph_format.first_line_indent = None
        left_paragraph.paragraph_format.space_after = Pt(0)
        left = left_paragraph.add_run(project_name)
        _format_run(left, size=8.5, color=MUTED_TEXT)

        right_paragraph = right_header.paragraphs[0]
        right_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        right_paragraph.paragraph_format.first_line_indent = None
        right_paragraph.paragraph_format.space_after = Pt(0)
        right = right_paragraph.add_run("投标文件初稿")
        _format_run(right, size=8.5, color=MUTED_TEXT, character_spacing=0.2)
        header._element.remove(header.paragraphs[0]._p)

        footer = section.footer
        table = footer.add_table(rows=1, cols=2, width=Cm(15.7))
        _set_table_geometry(table, [6800, 2101], indent=0)
        _remove_table_borders(table)
        left_cell, right_cell = table.rows[0].cells
        for cell in (left_cell, right_cell):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell, top=40, bottom=0, start=0, end=0)

        note_paragraph = left_cell.paragraphs[0]
        note_paragraph.paragraph_format.first_line_indent = None
        note_paragraph.paragraph_format.space_before = Pt(2)
        note_paragraph.paragraph_format.space_after = Pt(0)
        note = note_paragraph.add_run("AI 辅助生成 · 人工复核后使用")
        _format_run(note, size=8, color=MUTED_TEXT)

        page_paragraph = right_cell.paragraphs[0]
        page_paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        page_paragraph.paragraph_format.space_before = Pt(2)
        page_paragraph.paragraph_format.space_after = Pt(0)
        _add_page_number(page_paragraph)
        footer._element.remove(footer.paragraphs[0]._p)


def _remove_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            borders.append(element)
        element.set(qn("w:val"), "nil")


def _set_table_borders(table, color: str = BORDER) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "5")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def _set_table_border_side(table, *, side: str, color: str, size: int) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    border = borders.find(qn(f"w:{side}"))
    if border is None:
        border = OxmlElement(f"w:{side}")
        borders.append(border)
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), str(size))
    border.set(qn("w:space"), "0")
    border.set(qn("w:color"), color)


def _set_cell_margins(cell, *, top: int = 90, bottom: int = 90, start: int = 120, end: int = 120) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.find(qn("w:tcMar"))
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side, value in (("top", top), ("bottom", bottom), ("start", start), ("end", end)):
        element = tc_mar.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            tc_mar.append(element)
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")


def _set_cell_shading(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), fill)


def _set_cell_no_wrap(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    no_wrap = tc_pr.find(qn("w:noWrap"))
    if no_wrap is None:
        no_wrap = OxmlElement("w:noWrap")
        tc_pr.append(no_wrap)


def _set_table_geometry(table, widths: list[int], *, indent: int = TABLE_INDENT_DXA) -> None:
    total_width = sum(widths)
    if total_width <= 0 or len(widths) != len(table.columns):
        raise ValueError("Table widths must be positive and match the column count")
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    for tag, attributes in (
        ("w:tblW", {"w:w": str(total_width), "w:type": "dxa"}),
        ("w:tblInd", {"w:w": str(indent), "w:type": "dxa"}),
        ("w:tblLayout", {"w:type": "fixed"}),
    ):
        existing = tbl_pr.find(qn(tag))
        if existing is None:
            existing = OxmlElement(tag)
            tbl_pr.append(existing)
        for key, value in attributes.items():
            existing.set(qn(key), value)

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        column = OxmlElement("w:gridCol")
        column.set(qn("w:w"), str(width))
        grid.append(column)

    for row in table.rows:
        for cell, width in zip(row.cells, widths, strict=True):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_width = tc_pr.find(qn("w:tcW"))
            if tc_width is None:
                tc_width = OxmlElement("w:tcW")
                tc_pr.append(tc_width)
            tc_width.set(qn("w:w"), str(width))
            tc_width.set(qn("w:type"), "dxa")


def _repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    header = tr_pr.find(qn("w:tblHeader"))
    if header is None:
        header = OxmlElement("w:tblHeader")
        tr_pr.append(header)
    header.set(qn("w:val"), "true")


def _prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = tr_pr.find(qn("w:cantSplit"))
    if cant_split is None:
        cant_split = OxmlElement("w:cantSplit")
        tr_pr.append(cant_split)


def _style_table(
    table,
    widths: list[int],
    *,
    center_columns: set[int] | None = None,
    compact: bool = False,
) -> None:
    center_columns = center_columns or set()
    table.style = "Table Grid"
    _set_table_geometry(table, widths)
    _set_table_borders(table)
    _repeat_table_header(table.rows[0])
    for row_index, row in enumerate(table.rows):
        _prevent_row_split(row)
        for column_index, cell in enumerate(row.cells):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell, top=65 if compact else 90, bottom=65 if compact else 90)
            if row_index == 0:
                _set_cell_shading(cell, NAVY)
            elif row_index % 2 == 0:
                _set_cell_shading(cell, LIGHT_GRAY)
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.first_line_indent = None
                paragraph.paragraph_format.space_before = Pt(0.5 if compact else 1.5)
                paragraph.paragraph_format.space_after = Pt(0.5 if compact else 1.5)
                paragraph.paragraph_format.line_spacing = 1.1 if compact else 1.15
                paragraph.alignment = (
                    WD_ALIGN_PARAGRAPH.CENTER
                    if row_index == 0 or column_index in center_columns
                    else WD_ALIGN_PARAGRAPH.LEFT
                )
                for run in paragraph.runs:
                    _format_run(
                        run,
                        size=(9 if row_index else 9.3) if compact else (9.2 if row_index else 9.5),
                        bold=True if row_index == 0 else None,
                        color="FFFFFF" if row_index == 0 else DARK_TEXT,
                    )


def _column_widths(headers: list[str], rows: list[list[str]]) -> list[int]:
    column_count = len(headers)
    minimum = 760 if column_count >= 5 else 900
    text_lengths = []
    for index, header in enumerate(headers):
        values = [header, *[row[index] if index < len(row) else "" for row in rows]]
        text_lengths.append(max(5, min(36, max(len(value) for value in values))))
    remaining = CONTENT_WIDTH_DXA - minimum * column_count
    weight_total = sum(text_lengths)
    widths = [minimum + int(remaining * weight / weight_total) for weight in text_lengths]
    widths[-1] += CONTENT_WIDTH_DXA - sum(widths)
    return widths


def _add_cover(document: Document, analysis: TenderAnalysis) -> None:
    project_name = analysis.project_info.project_name or "未命名投标项目"

    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    kicker.paragraph_format.first_line_indent = None
    kicker.paragraph_format.space_before = Pt(74)
    kicker.paragraph_format.space_after = Pt(14)
    kicker_run = kicker.add_run("投 标 响 应 文 件")
    _format_run(
        kicker_run,
        latin=HEADING_FONT,
        east_asia=HEADING_FONT_EAST_ASIA,
        size=11.5,
        bold=True,
        color=BLUE,
        character_spacing=1.5,
    )

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.first_line_indent = None
    title.paragraph_format.space_after = Pt(10)
    title.paragraph_format.line_spacing = 1.15
    title_size = 28 if len(project_name) <= 18 else 24 if len(project_name) <= 30 else 20
    title_run = title.add_run(project_name)
    _format_run(
        title_run,
        latin=HEADING_FONT,
        east_asia=HEADING_FONT_EAST_ASIA,
        size=title_size,
        bold=True,
        color=NAVY,
        character_spacing=1.2,
    )

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.first_line_indent = None
    subtitle.paragraph_format.space_after = Pt(34)
    subtitle_run = subtitle.add_run("投标文件初稿")
    _format_run(
        subtitle_run,
        latin=HEADING_FONT,
        east_asia=HEADING_FONT_EAST_ASIA,
        size=16,
        bold=True,
        color=MUTED_TEXT,
        character_spacing=0.8,
    )

    metadata = document.add_table(rows=2, cols=4)
    _set_table_geometry(metadata, [1500, 2950, 1500, 2951], indent=0)
    _remove_table_borders(metadata)
    values = (
        ("招标人", analysis.project_info.purchaser or "待确认", "预算/限价", analysis.project_info.budget or "待确认"),
        ("投标截止", analysis.project_info.bid_deadline or "待确认", "生成日期", date.today().isoformat()),
    )
    for row, row_values in zip(metadata.rows, values, strict=True):
        for index, (cell, value) in enumerate(zip(row.cells, row_values, strict=True)):
            cell.text = value
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell, top=100, bottom=100, start=80, end=80)
            if index % 2 == 0:
                _set_cell_no_wrap(cell)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.first_line_indent = None
            paragraph.paragraph_format.space_before = Pt(2)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if index % 2 == 0 else WD_ALIGN_PARAGRAPH.LEFT
            _format_run(
                paragraph.runs[0],
                size=9.5 if index % 2 == 0 else 10,
                bold=index % 2 == 0,
                color=MUTED_TEXT if index % 2 == 0 else DARK_TEXT,
            )

    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note.paragraph_format.first_line_indent = None
    note.paragraph_format.left_indent = Cm(1.2)
    note.paragraph_format.right_indent = Cm(1.2)
    note.paragraph_format.space_before = Pt(42)
    note.paragraph_format.space_after = Pt(0)
    note.paragraph_format.line_spacing = 1.25
    _set_paragraph_shading(note, LIGHT_RED)
    _set_paragraph_border(note, side="left", color=RISK_RED, size=18, space=8)
    note_run = note.add_run("本文件由 AI 辅助生成，须经投标负责人逐条复核后使用")
    _format_run(note_run, size=9.5, bold=True, color=RISK_RED, character_spacing=0.2)
    document.add_page_break()


def _strip_markdown(value: str) -> str:
    return re.sub(
        r"\*\*(.*?)\*\*|`([^`]*)`",
        lambda match: match.group(1) or match.group(2) or "",
        value,
    ).strip()


def _add_list_item(document: Document, text: str, *, num_id: int, ordered: bool) -> None:
    paragraph = document.add_paragraph(style="List Number" if ordered else "List Bullet")
    paragraph.paragraph_format.first_line_indent = None
    paragraph.paragraph_format.space_after = Pt(4)
    paragraph.paragraph_format.line_spacing = 1.25
    paragraph.add_run(text)
    _apply_numbering(paragraph, num_id)


def _add_markdown(document: Document, markdown: str, *, bullet_num_id: int, decimal_num_id: int) -> None:
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        if not line:
            index += 1
            continue
        if line.startswith("### "):
            document.add_heading(_strip_markdown(line[4:]), level=3)
        elif line.startswith("## "):
            document.add_heading(_strip_markdown(line[3:]), level=2)
        elif line.startswith("# "):
            document.add_heading(_strip_markdown(line[2:]), level=1)
        elif line.startswith(("- ", "* ")):
            _add_list_item(
                document,
                _strip_markdown(line[2:]),
                num_id=bullet_num_id,
                ordered=False,
            )
        elif re.match(r"^\d+[.、]\s*", line):
            _add_list_item(
                document,
                _strip_markdown(re.sub(r"^\d+[.、]\s*", "", line)),
                num_id=decimal_num_id,
                ordered=True,
            )
        elif "|" in line and index + 1 < len(lines) and re.fullmatch(
            r"\s*\|?[\s:|-]+\|?\s*", lines[index + 1]
        ):
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                rows.append([cell.strip() for cell in lines[index].strip("|").split("|")])
                index += 1
            table = document.add_table(rows=1, cols=len(headers))
            for column, value in enumerate(headers):
                table.rows[0].cells[column].text = _strip_markdown(value)
            for row in rows:
                cells = table.add_row().cells
                for column in range(len(headers)):
                    cells[column].text = _strip_markdown(row[column] if column < len(row) else "")
            _style_table(table, _column_widths(headers, rows))
            continue
        else:
            paragraph = document.add_paragraph(_strip_markdown(line))
            paragraph.paragraph_format.first_line_indent = Cm(0.74)
            paragraph.paragraph_format.space_after = Pt(6)
            paragraph.paragraph_format.line_spacing = 1.5
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        index += 1


def _add_analysis_appendix(
    document: Document,
    analysis: TenderAnalysis,
    *,
    bullet_num_id: int,
) -> None:
    document.add_page_break()
    document.add_heading("生成依据与待核对事项", level=1)

    document.add_heading("强制要求", level=2)
    for item in analysis.mandatory_requirements:
        page = f"第 {item.source_page} 页" if item.source_page else "页码未知"
        _add_list_item(
            document,
            f"[{item.status}] {item.content}（{page}）",
            num_id=bullet_num_id,
            ordered=False,
        )

    document.add_heading("评分项", level=2)
    if analysis.scoring_items:
        table = document.add_table(rows=1, cols=4)
        for cell, value in zip(table.rows[0].cells, ("评分项", "分值", "页码", "状态"), strict=True):
            cell.text = value
        for item in analysis.scoring_items:
            cells = table.add_row().cells
            cells[0].text = item.criterion
            cells[1].text = item.points
            cells[2].text = str(item.source_page or "")
            cells[3].text = item.status
        _style_table(table, [4900, 1000, 1000, 2001], center_columns={1, 2, 3})
    else:
        document.add_paragraph("未识别到评分项，请人工检查招标文件。")

    document.add_heading("风险与待确认项", level=2)
    risk_items = [
        *analysis.risks,
        *[item for item in analysis.mandatory_requirements if item.status != "已确认"],
    ]
    for item in risk_items[:50]:
        _add_list_item(document, item.content, num_id=bullet_num_id, ordered=False)


def _add_review_appendix(document: Document, review: ReviewReport) -> None:
    document.add_page_break()
    document.add_heading("自动复核报告", level=1)
    summary = document.add_paragraph()
    summary.paragraph_format.first_line_indent = None
    summary.paragraph_format.space_before = Pt(2)
    summary.paragraph_format.space_after = Pt(10)
    summary.paragraph_format.line_spacing = 1.3
    _set_paragraph_shading(summary, LIGHT_BLUE if review.severity_count("高") == 0 else LIGHT_RED)
    _set_paragraph_border(
        summary,
        side="left",
        color=BLUE if review.severity_count("高") == 0 else RISK_RED,
        size=16,
        space=8,
    )
    summary_run = summary.add_run(
        f"待处理问题 {review.pending_count()} 项，其中高风险 {review.severity_count('高')} 项、"
        f"中风险 {review.severity_count('中')} 项、低风险 {review.severity_count('低')} 项。"
    )
    _format_run(
        summary_run,
        size=10,
        bold=True,
        color=RISK_RED if review.severity_count("高") else NAVY,
    )
    if not review.issues:
        document.add_paragraph("当前规则未发现问题，仍需由投标负责人完成最终审核。")
        return

    table = document.add_table(rows=1, cols=4)
    for cell, value in zip(table.rows[0].cells, ("级别 / 类别", "问题与处理建议", "页码", "状态"), strict=True):
        cell.text = value
    for item in review.issues:
        cells = table.add_row().cells
        cells[0].text = f"{item.severity}\n{item.category}"
        cells[1].text = ""
        message = cells[1].paragraphs[0]
        message_run = message.add_run(item.message)
        message_run.bold = True
        suggestion = cells[1].add_paragraph()
        suggestion_run = suggestion.add_run(f"建议：{item.suggestion}")
        suggestion_run.font.color.rgb = RGBColor.from_string(MUTED_TEXT)
        cells[2].text = str(item.source_page or "")
        cells[3].text = item.status
    _style_table(
        table,
        [1500, 5000, 700, 1701],
        center_columns={0, 2, 3},
        compact=True,
    )
    for row, issue in zip(table.rows[1:], review.issues, strict=True):
        if issue.severity == "高":
            for cell in row.cells:
                _set_cell_shading(cell, LIGHT_RED)
            for run in row.cells[0].paragraphs[0].runs:
                _format_run(run, size=9.2, bold=True, color=RISK_RED)


def _add_contents(document: Document, drafts: list[ChapterDraft]) -> None:
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    heading.paragraph_format.first_line_indent = None
    heading.paragraph_format.space_before = Pt(8)
    heading.paragraph_format.space_after = Pt(20)
    run = heading.add_run("目 录")
    _format_run(
        run,
        latin=HEADING_FONT,
        east_asia=HEADING_FONT_EAST_ASIA,
        size=20,
        bold=True,
        color=NAVY,
        character_spacing=1.5,
    )
    for index, draft in enumerate(drafts, start=1):
        entry = document.add_paragraph()
        entry.paragraph_format.first_line_indent = None
        entry.paragraph_format.left_indent = Cm(1.2)
        entry.paragraph_format.right_indent = Cm(1.2)
        entry.paragraph_format.space_after = Pt(7)
        entry.paragraph_format.line_spacing = 1.25
        entry_run = entry.add_run(f"第 {index} 章  {draft.title}")
        _format_run(entry_run, size=11, color=DARK_TEXT, character_spacing=0.2)
    document.add_page_break()


def _iter_table_paragraphs(table):
    for row in table.rows:
        for cell in row.cells:
            yield from cell.paragraphs
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested)


def _iter_all_paragraphs(document: Document):
    yield from document.paragraphs
    for table in document.tables:
        yield from _iter_table_paragraphs(table)
    for section in document.sections:
        for container in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            yield from container.paragraphs
            for table in container.tables:
                yield from _iter_table_paragraphs(table)


def _replace_paragraph_text(paragraph, placeholder: str, value: str) -> None:
    if placeholder not in paragraph.text:
        return
    for run in paragraph.runs:
        if placeholder in run.text:
            run.text = run.text.replace(placeholder, value)
            return
    start = paragraph.text.find(placeholder)
    end = start + len(placeholder)
    offset = 0
    started = False
    for run in paragraph.runs:
        run_start = offset
        run_end = offset + len(run.text)
        offset = run_end
        if run_end <= start or run_start >= end:
            continue
        prefix = run.text[: max(0, start - run_start)] if not started else ""
        suffix = run.text[max(0, end - run_start) :] if run_end >= end else ""
        run.text = f"{prefix}{value if not started else ''}{suffix}"
        started = True


def _apply_template_placeholders(document: Document, analysis: TenderAnalysis) -> None:
    info = analysis.project_info
    replacements = {
        "{{PROJECT_NAME}}": info.project_name,
        "{{PURCHASER}}": info.purchaser,
        "{{AGENCY}}": info.agency,
        "{{BUDGET}}": info.budget,
        "{{BID_DEADLINE}}": info.bid_deadline,
        "{{GENERATED_DATE}}": date.today().isoformat(),
    }
    for paragraph in _iter_all_paragraphs(document):
        for placeholder, value in replacements.items():
            _replace_paragraph_text(paragraph, placeholder, value or "")


def _add_qualification_images(document: Document, image_paths: list[Path]) -> None:
    valid_paths = [Path(path) for path in image_paths if Path(path).is_file()]
    if not valid_paths:
        return
    document.add_page_break()
    document.add_heading("资质证明材料", level=1)
    intro = document.add_paragraph("以下资质图片由系统按文件顺序自动编排，提交前请人工核对证书名称、有效期和清晰度。")
    intro.paragraph_format.first_line_indent = None
    intro.paragraph_format.space_after = Pt(12)

    section = document.sections[-1]
    usable_width_dxa = max(
        3000,
        int((section.page_width - section.left_margin - section.right_margin) / 635),
    )
    first_column = usable_width_dxa // 2
    table = document.add_table(rows=(len(valid_paths) + 1) // 2, cols=2)
    _style_table(table, [first_column, usable_width_dxa - first_column], compact=True)
    for row in table.rows:
        for cell in row.cells:
            _set_cell_shading(cell, "FFFFFF")
    for index, path in enumerate(valid_paths):
        cell = table.cell(index // 2, index % 2)
        cell.text = ""
        picture_paragraph = cell.paragraphs[0]
        picture_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture_paragraph.paragraph_format.first_line_indent = None
        picture_paragraph.paragraph_format.space_after = Pt(5)
        with Image.open(path) as image:
            width_px, height_px = image.size
        max_width_cm = min(7.0, max(3.0, first_column * 2.54 / 1440 - 0.8))
        scale = min(max_width_cm / max(width_px, 1), 9.8 / max(height_px, 1))
        width_cm = max(1.0, width_px * scale)
        height_cm = max(1.0, height_px * scale)
        picture_paragraph.add_run().add_picture(str(path), width=Cm(width_cm), height=Cm(height_cm))
        caption = cell.add_paragraph()
        caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
        caption.paragraph_format.first_line_indent = None
        caption.paragraph_format.space_after = Pt(4)
        caption_text = re.sub(r"[_-]+", " ", path.stem).strip() or f"资质材料 {index + 1}"
        _format_run(caption.add_run(caption_text), size=9.2, color=MUTED_TEXT)


def _add_generated_sections(
    document: Document,
    analysis: TenderAnalysis,
    drafts: list[ChapterDraft],
    review: ReviewReport | None,
    qualification_images: list[Path],
    *,
    include_contents: bool,
) -> None:
    bullet_num_id = _create_numbering(document, ordered=False)
    decimal_num_id = _create_numbering(document, ordered=True)
    if include_contents:
        _add_contents(document, drafts)
    for index, draft in enumerate(drafts, start=1):
        document.add_heading(f"第 {index} 章 {draft.title}", level=1)
        _add_markdown(
            document,
            draft.markdown,
            bullet_num_id=bullet_num_id,
            decimal_num_id=decimal_num_id,
        )
        if index < len(drafts):
            document.add_page_break()
    _add_analysis_appendix(document, analysis, bullet_num_id=bullet_num_id)
    if review is not None:
        _add_review_appendix(document, review)
    _add_qualification_images(document, qualification_images)


def _insert_template_content(
    document: Document,
    analysis: TenderAnalysis,
    drafts: list[ChapterDraft],
    review: ReviewReport | None,
    qualification_images: list[Path],
) -> None:
    placeholder = next(
        (paragraph for paragraph in document.paragraphs if paragraph.text.strip() == "{{BID_CONTENT}}"),
        None,
    )
    body = document._element.body
    existing = set(body.iterchildren())
    _add_generated_sections(
        document,
        analysis,
        drafts,
        review,
        qualification_images,
        include_contents=True,
    )
    if placeholder is None:
        return
    new_elements = [
        element for element in body.iterchildren()
        if element not in existing and element.tag != qn("w:sectPr")
    ]
    for element in new_elements:
        placeholder._p.addprevious(element)
    body.remove(placeholder._p)


def export_docx(
    output_path: str | Path,
    analysis: TenderAnalysis,
    drafts: list[ChapterDraft],
    review: ReviewReport | None = None,
    *,
    template_path: str | Path | None = None,
    qualification_images: list[str | Path] | None = None,
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [Path(path) for path in qualification_images or []]
    document = Document(str(template_path)) if template_path else Document()
    if not template_path:
        _configure_document(document)
    document.core_properties.title = analysis.project_info.project_name or "投标文件初稿"
    document.core_properties.subject = "AI 辅助生成的可复核投标文件初稿"
    document.core_properties.author = "投标初稿助手"

    if template_path:
        _apply_template_placeholders(document, analysis)
        _insert_template_content(document, analysis, drafts, review, images)
    else:
        _add_cover(document, analysis)
        _add_contents(document, drafts)
        _add_generated_sections(
            document,
            analysis,
            drafts,
            review,
            images,
            include_contents=False,
        )
        _add_header_footer(document, analysis.project_info.project_name or "未命名投标项目")
    document.save(target)
    return target
