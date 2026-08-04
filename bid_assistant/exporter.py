from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from bid_assistant.models import ChapterDraft, TenderAnalysis


def _set_east_asia_font(style, font_name: str) -> None:
    style.font.name = font_name
    style._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)


def _configure_document(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.8)
    section.right_margin = Cm(2.5)

    normal = document.styles["Normal"]
    _set_east_asia_font(normal, "宋体")
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.space_after = Pt(6)

    for name, size, color in (("Title", 26, "1F4E79"), ("Heading 1", 18, "1F4E79"), ("Heading 2", 15, "2F5597"), ("Heading 3", 13, "333333")):
        style = document.styles[name]
        _set_east_asia_font(style, "微软雅黑")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)


def _add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
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


def _add_header_footer(document: Document, project_name: str) -> None:
    for section in document.sections:
        header = section.header.paragraphs[0]
        header.text = f"{project_name} - 投标文件初稿"
        header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _set_east_asia_font(header.style, "宋体")
        header.runs[0].font.size = Pt(9)
        _add_page_number(section.footer.paragraphs[0])


def _add_cover(document: Document, analysis: TenderAnalysis) -> None:
    project_name = analysis.project_info.project_name or "未命名投标项目"
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_before = Pt(120)
    run = paragraph.add_run(project_name)
    run.bold = True
    run.font.size = Pt(28)
    run.font.color.rgb = RGBColor(31, 78, 121)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_before = Pt(26)
    subtitle_run = subtitle.add_run("投标文件初稿")
    subtitle_run.bold = True
    subtitle_run.font.size = Pt(22)

    for label, value in (
        ("招标人", analysis.project_info.purchaser or "待确认"),
        ("预算/限价", analysis.project_info.budget or "待确认"),
        ("投标截止时间", analysis.project_info.bid_deadline or "待确认"),
        ("生成日期", date.today().isoformat()),
    ):
        item = document.add_paragraph()
        item.alignment = WD_ALIGN_PARAGRAPH.CENTER
        item.add_run(f"{label}：{value}")

    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    note.paragraph_format.space_before = Pt(36)
    note_run = note.add_run("本文件由 AI 辅助生成，须经投标负责人逐条复核后使用")
    note_run.italic = True
    note_run.font.color.rgb = RGBColor(192, 0, 0)
    document.add_page_break()


def _strip_markdown(value: str) -> str:
    return re.sub(r"\*\*(.*?)\*\*|`([^`]*)`", lambda match: match.group(1) or match.group(2) or "", value).strip()


def _add_markdown(document: Document, markdown: str) -> None:
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
            document.add_paragraph(_strip_markdown(line[2:]), style="List Bullet")
        elif re.match(r"^\d+[.、]\s*", line):
            document.add_paragraph(_strip_markdown(re.sub(r"^\d+[.、]\s*", "", line)), style="List Number")
        elif "|" in line and index + 1 < len(lines) and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[index + 1]):
            headers = [cell.strip() for cell in line.strip("|").split("|")]
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                rows.append([cell.strip() for cell in lines[index].strip("|").split("|")])
                index += 1
            table = document.add_table(rows=1, cols=len(headers))
            table.style = "Table Grid"
            for column, value in enumerate(headers):
                table.rows[0].cells[column].text = _strip_markdown(value)
            for row in rows:
                cells = table.add_row().cells
                for column in range(len(headers)):
                    cells[column].text = _strip_markdown(row[column] if column < len(row) else "")
            continue
        else:
            paragraph = document.add_paragraph(_strip_markdown(line))
            paragraph.paragraph_format.first_line_indent = Cm(0.74)
        index += 1


def _add_analysis_appendix(document: Document, analysis: TenderAnalysis) -> None:
    document.add_page_break()
    document.add_heading("生成依据与待核对事项", level=1)

    document.add_heading("强制要求", level=2)
    for item in analysis.mandatory_requirements:
        page = f"第 {item.source_page} 页" if item.source_page else "页码未知"
        document.add_paragraph(f"[{item.status}] {item.content}（{page}）", style="List Bullet")

    document.add_heading("评分项", level=2)
    if analysis.scoring_items:
        table = document.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, value in zip(table.rows[0].cells, ("评分项", "分值", "页码", "状态"), strict=True):
            cell.text = value
        for item in analysis.scoring_items:
            cells = table.add_row().cells
            cells[0].text = item.criterion
            cells[1].text = item.points
            cells[2].text = str(item.source_page or "")
            cells[3].text = item.status
    else:
        document.add_paragraph("未识别到评分项，请人工检查招标文件。")

    document.add_heading("风险与待确认项", level=2)
    risk_items = [*analysis.risks, *[item for item in analysis.mandatory_requirements if item.status != "已确认"]]
    for item in risk_items[:50]:
        document.add_paragraph(item.content, style="List Bullet")


def export_docx(
    output_path: str | Path,
    analysis: TenderAnalysis,
    drafts: list[ChapterDraft],
) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    _configure_document(document)
    _add_cover(document, analysis)

    document.add_heading("目录", level=1)
    for index, draft in enumerate(drafts, start=1):
        document.add_paragraph(f"{index}. {draft.title}")
    document.add_page_break()

    for index, draft in enumerate(drafts, start=1):
        document.add_heading(f"第 {index} 章 {draft.title}", level=1)
        _add_markdown(document, draft.markdown)
        if index < len(drafts):
            document.add_page_break()

    _add_analysis_appendix(document, analysis)
    _add_header_footer(document, analysis.project_info.project_name or "未命名投标项目")
    document.save(target)
    return target
