from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bid_assistant.models import ReviewReport, SubmissionItem
from bid_assistant.submission import ATTACHMENT_CATEGORY_LABELS, summarize_submission_items


PACKAGE_FORMAT = "bid-assistant-submission-package"
PACKAGE_FORMAT_VERSION = 1


def build_package_readiness(
    word_version: dict | None,
    items: list[SubmissionItem],
    attachment_refs: set[str],
    review: ReviewReport | None,
    *,
    has_unsaved_changes: bool = False,
) -> dict:
    summary = summarize_submission_items(items, attachment_refs)
    checks = [
        {
            "key": "word",
            "label": "Word 版本",
            "status": "pass" if word_version else "block",
            "detail": (
                f"已选择 V{word_version['version']:03d}：{word_version['filename']}"
                if word_version
                else "尚未生成或选择 Word 版本。"
            ),
        },
        {
            "key": "checklist",
            "label": "材料清单",
            "status": "pass" if items else "block",
            "detail": f"清单共 {len(items)} 项。" if items else "尚未生成最终提交材料清单。",
        },
        {
            "key": "saved",
            "label": "清单保存状态",
            "status": "block" if has_unsaved_changes else "pass",
            "detail": "存在未保存修改，请先保存清单。" if has_unsaved_changes else "当前清单已保存。",
        },
        {
            "key": "required",
            "label": "必交项",
            "status": "block" if summary["pending_required"] else "pass",
            "detail": (
                f"仍有 {summary['pending_required']} 个必交项待准备。"
                if summary["pending_required"]
                else "所有必交项均已备妥或明确不适用。"
            ),
        },
        {
            "key": "links",
            "label": "附件关联",
            "status": "block" if summary["broken_links"] else "pass",
            "detail": (
                f"有 {summary['broken_links']} 个关联附件不存在。"
                if summary["broken_links"]
                else f"已关联 {summary['linked']} 个附件，未发现断链。"
            ),
        },
    ]

    if review is None:
        review_status = "warning"
        review_detail = "尚未生成复核报告，本次只能作为内部预审包。"
        pending_review = 0
        high_risk = 0
    else:
        pending_review = review.pending_count()
        high_risk = review.severity_count("高")
        review_status = "warning" if pending_review else "pass"
        review_detail = (
            f"仍有 {pending_review} 个复核问题待处理，其中高风险 {high_risk} 个。"
            if pending_review
            else "复核问题均已处理或忽略。"
        )
    checks.append(
        {
            "key": "review",
            "label": "复核状态",
            "status": review_status,
            "detail": review_detail,
        }
    )

    blocking_count = sum(item["status"] == "block" for item in checks)
    warning_count = sum(item["status"] == "warning" for item in checks)
    return {
        "checks": checks,
        "can_package": blocking_count == 0,
        "requires_confirmation": warning_count > 0,
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "pending_review": pending_review,
        "high_risk": high_risk,
        "submission_summary": summary,
    }


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _checklist_csv(items: list[SubmissionItem]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=["类别", "材料名称", "原文页码", "必交", "状态", "关联附件", "备注"],
    )
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "类别": item.category,
                "材料名称": item.name,
                "原文页码": item.source_page or "",
                "必交": "是" if item.required else "否",
                "状态": item.status,
                "关联附件": item.attachment,
                "备注": item.note,
            }
        )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def create_submission_package(
    output_path: str | Path,
    *,
    project: dict,
    word_version: dict,
    items: list[SubmissionItem],
    attachment_files: dict[str, list[Path]],
    review_summary: dict[str, int],
    internal_review_only: bool,
    note: str = "",
) -> dict:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    package_files: list[tuple[str, bytes]] = []

    word_content = word_version["path"].read_bytes()
    package_files.append((f"投标文件/{word_version['filename']}", word_content))

    for category_id, paths in attachment_files.items():
        category_label = ATTACHMENT_CATEGORY_LABELS[category_id]
        for path in paths:
            package_files.append((f"提交附件/{category_label}/{path.name}", path.read_bytes()))

    checklist_content = _checklist_csv(items)
    package_files.append(("最终提交材料清单.csv", checklist_content))

    warning_text = "是，仅供内部复核" if internal_review_only else "否，可进入最终提交复核"
    readme = (
        f"项目名称：{project['name']}\r\n"
        f"生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}\r\n"
        f"Word 版本：V{word_version['version']:03d} {word_version['filename']}\r\n"
        f"内部预审包：{warning_text}\r\n"
        f"版本说明：{note.strip() or '-'}\r\n"
        "\r\n提交前请再次核对签字、盖章、密封、介质和平台上传要求。\r\n"
    ).encode("utf-8-sig")
    package_files.append(("提交包说明.txt", readme))

    file_records = [
        {"path": name, "size": len(content), "sha256": _sha256_bytes(content)}
        for name, content in package_files
    ]
    manifest = {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "project": {"id": project["id"], "name": project["name"]},
        "internal_review_only": internal_review_only,
        "note": note.strip()[:200],
        "word": {
            "version": word_version["version"],
            "filename": word_version["filename"],
            "sha256": word_version.get("sha256", _sha256_bytes(word_content)),
        },
        "checklist": summarize_submission_items(
            items,
            {
                f"{ATTACHMENT_CATEGORY_LABELS[category_id]}/{path.name}"
                for category_id, paths in attachment_files.items()
                for path in paths
            },
        ),
        "review": {key: max(0, int(value)) for key, value in review_summary.items()},
        "files": file_records,
    }
    manifest_content = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in package_files:
            archive.writestr(name, content)
        archive.writestr("package-manifest.json", manifest_content)
    temporary.replace(target)
    return manifest
