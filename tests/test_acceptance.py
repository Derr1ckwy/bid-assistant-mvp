from bid_assistant.acceptance import build_acceptance_report, build_analysis_acceptance
from bid_assistant.models import RequirementItem, ScoringItem, TenderAnalysis


def _baseline() -> TenderAnalysis:
    return TenderAnalysis(
        mandatory_requirements=[
            RequirementItem(id="req_keep", content="必须提交营业执照", status="待确认"),
            RequirementItem(id="req_ignore", content="须提供无关材料", status="待确认"),
        ],
        scoring_items=[
            ScoringItem(id="score_keep", criterion="技术方案 20 分", status="待确认")
        ],
        analysis_mode="rules",
    )


def test_acceptance_compares_baseline_with_human_review() -> None:
    current = TenderAnalysis(
        mandatory_requirements=[
            RequirementItem(
                id="req_keep",
                content="必须提交营业执照复印件",
                status="已确认",
            ),
            RequirementItem(id="req_ignore", content="须提供无关材料", status="忽略"),
            RequirementItem(id="req_manual", content="须提交授权书", status="已确认"),
        ],
        scoring_items=[
            ScoringItem(id="score_keep", criterion="技术方案 20 分", status="已确认")
        ],
        analysis_mode="rules",
    )

    record = build_analysis_acceptance(
        _baseline(),
        current,
        source_filename="真实招标文件.pdf",
        reviewer="张三",
        manual_minutes=120,
        assisted_minutes=40,
    )

    assert record["complete"] is True
    assert record["baseline_count"] == 3
    assert record["accepted_count"] == 2
    assert record["rejected_count"] == 1
    assert record["manual_addition_count"] == 1
    assert record["edited_count"] == 1
    assert record["pending_count"] == 0
    assert record["reviewed_hit_rate_percent"] == 66.7
    assert record["estimated_coverage_percent"] == 66.7
    assert record["time_saved_minutes"] == 80
    assert record["time_saving_percent"] == 66.7


def test_acceptance_counts_deleted_baseline_item_as_rejected() -> None:
    current = TenderAnalysis(
        mandatory_requirements=[
            RequirementItem(id="req_keep", content="必须提交营业执照", status="已确认")
        ],
        analysis_mode="rules",
    )

    record = build_analysis_acceptance(_baseline(), current)

    assert record["rejected_count"] == 2
    assert {item["result"] for item in record["rejected_items"]} == {"人工删除"}


def test_acceptance_stays_preliminary_while_items_are_pending() -> None:
    current = _baseline()
    current.mandatory_requirements.append(
        RequirementItem(id="req_manual", content="人工发现的新要求", status="待核对")
    )

    record = build_analysis_acceptance(_baseline(), current)

    assert record["complete"] is False
    assert record["pending_count"] == 4
    assert record["estimated_coverage_percent"] is None
    assert any("覆盖率暂不计算" in item for item in record["warnings"])


def test_acceptance_report_is_utf8_bom_and_contains_details() -> None:
    current = TenderAnalysis(
        mandatory_requirements=[
            RequirementItem(id="req_keep", content="必须提交营业执照", status="已确认"),
            RequirementItem(id="req_ignore", content="须提供无关材料", status="忽略"),
        ],
        scoring_items=[
            ScoringItem(id="score_keep", criterion="技术方案 20 分", status="已确认")
        ],
        analysis_mode="rules",
    )
    record = build_analysis_acceptance(_baseline(), current, reviewer="李四", notes="用于首轮验收")

    report = build_acceptance_report(record)
    text = report.decode("utf-8-sig")

    assert report.startswith(b"\xef\xbb\xbf")
    assert "验收状态：完整样本" in text
    assert "已审核项命中率：66.7%" in text
    assert "须提供无关材料" in text
    assert "用于首轮验收" in text


def test_acceptance_marks_historical_project_baseline() -> None:
    record = build_analysis_acceptance(
        _baseline(),
        _baseline(),
        baseline_origin="历史项目当前结果",
    )
    report = build_acceptance_report(record).decode("utf-8-sig")

    assert record["baseline_origin"] == "历史项目当前结果"
    assert any("只统计建基线之后的变化" in item for item in record["warnings"])
    assert "基线来源：历史项目当前结果" in report


def test_acceptance_blocks_mismatched_source() -> None:
    current = _baseline()
    for item in current.mandatory_requirements:
        item.status = "已确认"
    for item in current.scoring_items:
        item.status = "已确认"

    record = build_analysis_acceptance(_baseline(), current, source_matches=False)
    report = build_acceptance_report(record).decode("utf-8-sig")

    assert record["complete"] is False
    assert any("来源不一致" in item for item in record["warnings"])
    assert "源文件一致性：不一致" in report
