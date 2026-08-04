from bid_assistant.generator import generate_chapter
from bid_assistant.models import ChapterPlan, ProjectInfo, TenderAnalysis


def test_rule_draft_marks_missing_company_facts() -> None:
    plan = ChapterPlan(title="技术方案", instructions="说明方案")
    analysis = TenderAnalysis(project_info=ProjectInfo(project_name="测试项目"))

    draft = generate_chapter(plan, analysis, [], use_llm=False)

    assert draft.title == "技术方案"
    assert "待补充" in draft.markdown
    assert "待确认" in draft.markdown
