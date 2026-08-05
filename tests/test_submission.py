from pathlib import Path

from bid_assistant.models import ProjectInfo, RequirementItem, SubmissionItem, TenderAnalysis
from bid_assistant.submission import (
    build_attachment_inventory,
    summarize_submission_items,
    sync_submission_items,
)


def test_sync_submission_items_preserves_manual_work_and_adds_new_requirements() -> None:
    retained_requirement = RequirementItem(id="req_keep", content="提交营业执照复印件", source_page=3)
    new_requirement = RequirementItem(id="req_new", content="提交技术偏离表", source_page=8)
    ignored_requirement = RequirementItem(id="req_ignore", content="旧材料", status="忽略")
    analysis = TenderAnalysis(
        project_info=ProjectInfo(project_name="测试项目"),
        required_documents=[retained_requirement, new_requirement, ignored_requirement],
    )
    existing = [
        SubmissionItem(
            id="submit_keep",
            category="资格文件",
            name="旧名称",
            source_requirement_id="req_keep",
            status="已备妥",
            attachment="资格文件/营业执照.pdf",
            note="已盖章",
        ),
        SubmissionItem(id="submit_manual", category="其他", name="人工新增封装检查", required=False),
        SubmissionItem(id="submit_removed", name="已删除的分析材料", source_requirement_id="req_removed"),
    ]

    synced = sync_submission_items(analysis, existing)

    assert [item.id for item in synced] == ["submit_keep", synced[1].id, "submit_manual"]
    assert synced[0].name == "提交营业执照复印件"
    assert synced[0].source_page == 3
    assert synced[0].status == "已备妥"
    assert synced[0].attachment == "资格文件/营业执照.pdf"
    assert synced[0].note == "已盖章"
    assert synced[1].category == "技术文件"
    assert all(item.source_requirement_id != "req_ignore" for item in synced)


def test_submission_summary_tracks_required_items_and_broken_links() -> None:
    items = [
        SubmissionItem(name="营业执照", status="已备妥", attachment="资格文件/营业执照.pdf"),
        SubmissionItem(name="报价表", status="待准备"),
        SubmissionItem(name="纸质封条", required=False, status="不适用", attachment="其他附件/缺失.pdf"),
    ]

    summary = summarize_submission_items(items, {"资格文件/营业执照.pdf"})

    assert summary["total"] == 3
    assert summary["required"] == 2
    assert summary["ready"] == 1
    assert summary["pending_required"] == 1
    assert summary["linked"] == 2
    assert summary["broken_links"] == 1
    assert summary["complete"] is False

    empty_summary = summarize_submission_items([], set())
    assert empty_summary["complete"] is True


def test_attachment_inventory_is_generated_from_real_files(tmp_path: Path) -> None:
    license_path = tmp_path / "营业执照.pdf"
    quote_path = tmp_path / "报价表.xlsx"
    license_path.write_bytes(b"license")
    quote_path.write_bytes(b"quote")

    items = build_attachment_inventory(
        {
            "qualification": [license_path],
            "pricing": [quote_path],
        }
    )

    assert [item.name for item in items] == ["营业执照.pdf", "报价表.xlsx"]
    assert all(item.status == "已备妥" for item in items)
    assert items[0].attachment == "资格文件/营业执照.pdf"
    assert items[1].attachment == "报价文件/报价表.xlsx"
