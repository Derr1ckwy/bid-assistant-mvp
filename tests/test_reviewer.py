from bid_assistant.models import (
    ChapterDraft,
    ChapterPlan,
    ProjectInfo,
    RequirementItem,
    TenderAnalysis,
)
from bid_assistant.reviewer import build_review_report


def test_review_report_finds_missing_and_unconfirmed_content() -> None:
    plan = ChapterPlan(title="资格、业绩与证明材料")
    analysis = TenderAnalysis(
        project_info=ProjectInfo(project_name="测试项目"),
        mandatory_requirements=[
            RequirementItem(content="必须提交营业执照。", source_page=3, source_quote="必须提交营业执照。")
        ],
        risks=[
            RequirementItem(content="未提交材料将被否决。", category="废标风险", source_page=4)
        ],
        outline=[plan],
    )
    drafts = [
        ChapterDraft(
            chapter_id=plan.id,
            title=plan.title,
            markdown="## 证明材料\n\n企业业绩待补充，人员信息待确认。",
        )
    ]

    report = build_review_report(analysis, drafts)
    categories = {item.category for item in report.issues}

    assert report.pending_count() == len(report.issues)
    assert report.severity_count("高") >= 3
    assert "项目信息" in categories
    assert "强制要求" in categories
    assert "废标风险" in categories
    assert "正文占位符" in categories
    assert "资料依据" in categories


def test_review_report_detects_cross_chapter_duration_conflict() -> None:
    analysis = TenderAnalysis(
        project_info=ProjectInfo(
            project_name="测试项目",
            purchaser="测试采购人",
            budget="100 万元",
            bid_deadline="2026-09-01",
            agency="测试代理机构",
        )
    )
    drafts = [
        ChapterDraft(chapter_id="a", title="实施计划", markdown="项目建设工期为 120 日。"),
        ChapterDraft(chapter_id="b", title="服务承诺", markdown="本项目交付期为 90 日。"),
    ]

    report = build_review_report(analysis, drafts)

    assert any(item.category == "跨章节一致性" and item.severity == "高" for item in report.issues)
