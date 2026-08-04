from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm

from bid_assistant.exporter import export_docx
from bid_assistant.models import (
    ChapterDraft,
    ProjectInfo,
    RequirementItem,
    ReviewIssue,
    ReviewReport,
    ScoringItem,
    TenderAnalysis,
)


def test_export_docx_can_be_reopened(tmp_path: Path) -> None:
    output = tmp_path / "draft.docx"
    analysis = TenderAnalysis(
        project_info=ProjectInfo(
            project_name="智慧园区项目",
            purchaser="测试采购单位",
            budget="180 万元",
            bid_deadline="2026-09-15 09:30",
        ),
        mandatory_requirements=[
            RequirementItem(content="必须提交营业执照。", source_page=3, status="已确认")
        ],
        scoring_items=[
            ScoringItem(criterion="技术方案完整性", points="20", source_page=8)
        ],
    )
    drafts = [
        ChapterDraft(
            chapter_id="chapter_1",
            title="技术方案",
            markdown="## 总体设计\n\n1. 建立统一平台。\n\n- 支持人工复核。",
        )
    ]
    review = ReviewReport(
        issues=[
            ReviewIssue(
                severity="高",
                category="废标风险",
                message="必须复核营业执照。",
                suggestion="确认文件有效期。",
            )
        ]
    )

    result = export_docx(output, analysis, drafts, review)
    reopened = Document(result)
    text = "\n".join(paragraph.text for paragraph in reopened.paragraphs)
    footer_xml = reopened.sections[0].footer._element.xml
    first_header_xml = reopened.sections[0].first_page_header._element.xml
    first_footer_xml = reopened.sections[0].first_page_footer._element.xml

    assert result.exists()
    assert "智慧园区项目" in text
    assert "第 1 章 技术方案" in text
    assert "生成依据与待核对事项" in text
    assert "自动复核报告" in text
    assert "PAGE" in footer_xml
    assert reopened.sections[0].different_first_page_header_footer is True
    assert "智慧园区项目" not in first_header_xml
    assert "PAGE" not in first_footer_xml
    assert len(reopened.sections[0].footer.tables) == 1
    footer_table = reopened.sections[0].footer.tables[0]
    assert footer_table.rows[0].cells[0].text == "AI 辅助生成 · 人工复核后使用"
    assert footer_table.rows[0].cells[1].paragraphs[0].alignment == 2
    assert len(reopened.sections[0].header.tables) == 1
    header_table = reopened.sections[0].header.tables[0]
    assert header_table.rows[0].cells[0].text == "智慧园区项目"
    assert header_table.rows[0].cells[1].paragraphs[0].alignment == 2

    section = reopened.sections[0]
    assert abs(section.page_width - Cm(21)) < 1000
    assert abs(section.page_height - Cm(29.7)) < 1000

    normal = reopened.styles["Normal"]
    assert normal.font.size.pt == 10.5
    assert normal.paragraph_format.line_spacing == 1.5
    assert normal._element.xml.find('w:eastAsia="宋体"') >= 0

    cover_title = next(paragraph for paragraph in reopened.paragraphs if paragraph.text == "智慧园区项目")
    assert cover_title.runs[0]._element.find(qn("w:rPr")).find(qn("w:spacing")) is not None

    cover_metadata = reopened.tables[0]
    assert [cell.width for cell in cover_metadata.rows[0].cells] == [
        1500 * 635,
        2950 * 635,
        1500 * 635,
        2951 * 635,
    ]
    for index in (0, 2):
        assert cover_metadata.rows[0].cells[index]._tc.tcPr.find(qn("w:noWrap")) is not None

    assert len(reopened.tables[-1].columns) == 4
    for row in reopened.tables[-1].rows:
        assert row._tr.trPr.find(qn("w:cantSplit")) is not None
    for table in reopened.tables:
        layout = table._tbl.tblPr.find(qn("w:tblLayout"))
        width = table._tbl.tblPr.find(qn("w:tblW"))
        assert layout is not None and layout.get(qn("w:type")) == "fixed"
        assert width is not None and width.get(qn("w:type")) == "dxa"
