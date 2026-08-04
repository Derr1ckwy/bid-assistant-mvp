import hashlib
import json
import zipfile
from pathlib import Path

from bid_assistant.models import ReviewIssue, ReviewReport, SubmissionItem
from bid_assistant.packager import (
    build_package_readiness,
    build_package_verification_report,
    create_submission_package,
    verify_submission_package,
)


def _word_version(path: Path) -> dict:
    return {
        "id": "exportv_test",
        "version": 2,
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "path": path,
    }


def _create_minimal_package(
    tmp_path: Path,
    *,
    internal_review_only: bool = False,
    pending_review: int = 0,
) -> Path:
    word_path = tmp_path / "投标文件_V001.docx"
    word_path.write_bytes(b"word-content")
    license_path = tmp_path / "营业执照.pdf"
    license_path.write_bytes(b"license-content")
    target = tmp_path / "提交包_P001.zip"
    create_submission_package(
        target,
        project={"id": "proj_test", "name": "测试项目"},
        word_version=_word_version(word_path),
        items=[
            SubmissionItem(
                category="资格文件",
                name="营业执照",
                status="已备妥",
                attachment="资格文件/营业执照.pdf",
            )
        ],
        attachment_files={"qualification": [license_path]},
        review_summary={"pending": pending_review, "high": pending_review, "medium": 0, "low": 0},
        internal_review_only=internal_review_only,
    )
    return target


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


def test_package_readiness_uses_word_quality_as_block_or_warning(tmp_path: Path) -> None:
    word_path = tmp_path / "投标文件_V001.docx"
    word_path.write_bytes(b"docx")
    items = [SubmissionItem(name="营业执照", status="已备妥")]

    blocked = build_package_readiness(
        _word_version(word_path),
        items,
        set(),
        ReviewReport(),
        word_quality={"valid": False, "errors": ["文件损坏"], "warnings": []},
    )
    warned = build_package_readiness(
        _word_version(word_path),
        items,
        set(),
        ReviewReport(),
        word_quality={"valid": True, "errors": [], "warnings": ["存在占位符"]},
    )

    assert blocked["can_package"] is False
    assert any(item["key"] == "word_quality" and item["status"] == "block" for item in blocked["checks"])
    assert warned["can_package"] is True
    assert warned["requires_confirmation"] is True
    assert any(item["key"] == "word_quality" and item["status"] == "warning" for item in warned["checks"])


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

    package_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    verification = verify_submission_package(target, expected_sha256=package_sha256)
    report = build_package_verification_report(
        verification,
        package_version={
            "version": 1,
            "word_version": 3,
            "word_filename": word_path.name,
            "note": "第一次内部预审",
        },
    )

    assert verification["valid"] is True
    assert verification["file_count"] == 5
    assert all(item["verified"] for item in verification["files"])
    assert verification["warnings"] == ["该提交包标记为仅供内部预审，不可直接作为最终递交依据。"]
    assert report.startswith(b"\xef\xbb\xbf")
    report_text = report.decode("utf-8-sig")
    assert "校验结论：通过" in report_text
    assert "提交包版本：P001" in report_text
    assert "[通过] 投标文件/项目投标文件_V003.docx" in report_text


def test_package_verifier_detects_outer_and_inner_tampering(tmp_path: Path) -> None:
    target = _create_minimal_package(tmp_path)
    expected_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}
    entries["提交附件/资格文件/营业执照.pdf"] = b"tampered-license"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)

    verification = verify_submission_package(target, expected_sha256=expected_sha256)

    assert verification["valid"] is False
    assert "提交包文件 SHA-256 与版本记录不一致。" in verification["errors"]
    assert "文件大小校验失败：提交附件/资格文件/营业执照.pdf" in verification["errors"]


def test_package_verifier_rejects_unsafe_archive_paths(tmp_path: Path) -> None:
    target = _create_minimal_package(tmp_path)
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr("../escape.txt", b"unsafe")
        archive.writestr("extra.txt", b"unlisted")

    verification = verify_submission_package(target)

    assert verification["valid"] is False
    assert "提交包包含不安全路径：../escape.txt" in verification["errors"]
    assert "提交包包含未登记文件：extra.txt" in verification["errors"]


def test_package_verifier_requires_internal_flag_for_pending_review(tmp_path: Path) -> None:
    target = _create_minimal_package(tmp_path, pending_review=2)

    verification = verify_submission_package(target)

    assert verification["valid"] is False
    assert "存在待处理复核问题，但提交包未标记为内部预审。" in verification["errors"]
