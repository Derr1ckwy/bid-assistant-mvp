import hashlib
import json
import zipfile
from pathlib import Path

from bid_assistant.models import ReviewIssue, ReviewReport, SubmissionItem
from bid_assistant.packager import build_package_readiness, create_submission_package


def _word_version(path: Path) -> dict:
    return {
        "id": "exportv_test",
        "version": 2,
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "path": path,
    }


def test_package_readiness_blocks_incomplete_or_unsaved_materials(tmp_path: Path) -> None:
    word_path = tmp_path / "投标文件_V002.docx"
    word_path.write_bytes(b"docx")
    review = ReviewReport()
    items = [
        SubmissionItem(name="营业执照", status="待准备"),
        SubmissionItem(name="报价表", status="已备妥", attachment="报价文件/报价表.xlsx"),
    ]

    readiness = build_package_readiness(
        _word_version(word_path),
        items,
        set(),
        review,
        has_unsaved_changes=True,
    )

    blocked_keys = {item["key"] for item in readiness["checks"] if item["status"] == "block"}
    assert readiness["can_package"] is False
    assert blocked_keys == {"saved", "required", "links"}

    empty_readiness = build_package_readiness(None, [], set(), review)
    empty_blocked_keys = {
        item["key"] for item in empty_readiness["checks"] if item["status"] == "block"
    }
    assert empty_blocked_keys == {"word", "checklist"}


def test_package_readiness_allows_internal_review_after_warning_confirmation(tmp_path: Path) -> None:
    word_path = tmp_path / "投标文件_V001.docx"
    word_path.write_bytes(b"docx")
    items = [SubmissionItem(name="营业执照", status="已备妥")]
    review = ReviewReport(
        issues=[
            ReviewIssue(
                severity="高",
                category="废标风险",
                message="签章状态仍需人工核对",
            )
        ]
    )

    readiness = build_package_readiness(_word_version(word_path), items, set(), review)

    assert readiness["can_package"] is True
    assert readiness["requires_confirmation"] is True
    assert readiness["blocking_count"] == 0
    assert readiness["warning_count"] == 1
    assert readiness["pending_review"] == 1
    assert readiness["high_risk"] == 1


def test_submission_package_contains_manifest_checksums_and_chinese_checklist(tmp_path: Path) -> None:
    word_path = tmp_path / "项目投标文件_V003.docx"
    word_path.write_bytes(b"word-content")
    license_path = tmp_path / "营业执照.pdf"
    license_path.write_bytes(b"license-content")
    quote_path = tmp_path / "报价表.xlsx"
    quote_path.write_bytes(b"quote-content")
    items = [
        SubmissionItem(
            category="资格文件",
            name="营业执照",
            source_page=3,
            status="已备妥",
            attachment="资格文件/营业执照.pdf",
            note="已盖章",
        ),
        SubmissionItem(
            category="报价文件",
            name="报价表",
            source_page=12,
            status="已备妥",
            attachment="报价文件/报价表.xlsx",
        ),
    ]
    target = tmp_path / "最终提交包_P001.zip"

    returned_manifest = create_submission_package(
        target,
        project={"id": "proj_test", "name": "测试项目"},
        word_version=_word_version(word_path),
        items=items,
        attachment_files={
            "qualification": [license_path],
            "pricing": [quote_path],
        },
        review_summary={"pending": 1, "high": 1, "medium": 0, "low": 0},
        internal_review_only=True,
        note="第一次内部预审",
    )

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert names == {
            "投标文件/项目投标文件_V003.docx",
            "提交附件/资格文件/营业执照.pdf",
            "提交附件/报价文件/报价表.xlsx",
            "最终提交材料清单.csv",
            "提交包说明.txt",
            "package-manifest.json",
        }

        manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
        assert manifest == returned_manifest
        assert manifest["internal_review_only"] is True
        assert manifest["note"] == "第一次内部预审"
        assert manifest["checklist"]["complete"] is True
        assert manifest["checklist"]["broken_links"] == 0

        for record in manifest["files"]:
            content = archive.read(record["path"])
            assert record["size"] == len(content)
            assert record["sha256"] == hashlib.sha256(content).hexdigest()

        checklist = archive.read("最终提交材料清单.csv")
        assert checklist.startswith(b"\xef\xbb\xbf")
        checklist_text = checklist.decode("utf-8-sig")
        assert checklist_text.splitlines()[0] == "类别,材料名称,原文页码,必交,状态,关联附件,备注"
        assert "资格文件,营业执照,3,是,已备妥,资格文件/营业执照.pdf,已盖章" in checklist_text
