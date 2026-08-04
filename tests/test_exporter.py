from pathlib import Path

from docx import Document

from bid_assistant.exporter import export_docx
from bid_assistant.models import ChapterDraft, ProjectInfo, RequirementItem, ScoringItem, TenderAnalysis


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

    result = export_docx(output, analysis, drafts)
    reopened = Document(result)
    text = "\n".join(paragraph.text for paragraph in reopened.paragraphs)
    footer_xml = reopened.sections[0].footer._element.xml

    assert result.exists()
    assert "智慧园区项目" in text
    assert "第 1 章 技术方案" in text
    assert "生成依据与待核对事项" in text
    assert "PAGE" in footer_xml
