from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bid_assistant.models import ReviewReport, SubmissionItem
from bid_assistant.submission import ATTACHMENT_CATEGORY_LABELS, summarize_submission_items


PACKAGE_FORMAT = "bid-assistant-submission-package"
PACKAGE_FORMAT_VERSION = 1
MAX_PACKAGE_FILES = 500
MAX_PACKAGE_MANIFEST_BYTES = 1024 * 1024
MAX_PACKAGE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_package_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[a-zA-Z]:", value):
        return False
    parts = value.split("/")
    return not any(
        part in {"", ".", ".."}
        or part.rstrip(" .") != part
        or re.search(r'[<>:"|?*\x00-\x1f]', part)
        for part in parts
    )


def _is_nonnegative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def verify_submission_package(
    package_path: str | Path,
    *,
    expected_sha256: str = "",
) -> dict:
    target = Path(package_path)
    result = {
        "valid": False,
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "filename": target.name,
        "size": 0,
        "sha256": "",
        "expected_sha256": expected_sha256,
        "file_count": 0,
        "content_size": 0,
        "errors": [],
        "warnings": [],
        "files": [],
        "manifest": None,
    }
    errors: list[str] = result["errors"]
    warnings: list[str] = result["warnings"]

    if not target.is_file():
        errors.append("提交包文件不存在。")
        return result

    try:
        result["size"] = target.stat().st_size
        result["sha256"] = _sha256_path(target)
    except OSError:
        errors.append("提交包文件无法读取。")
        return result

    if expected_sha256:
        if not _SHA256_PATTERN.fullmatch(expected_sha256):
            errors.append("版本记录中的提交包 SHA-256 格式无效。")
        elif result["sha256"] != expected_sha256:
            errors.append("提交包文件 SHA-256 与版本记录不一致。")

    try:
        archive = zipfile.ZipFile(target, "r")
    except (OSError, zipfile.BadZipFile):
        errors.append("文件不是有效的 ZIP 提交包。")
        return result

    with archive:
        infos = archive.infolist()
        file_infos = [info for info in infos if not info.is_dir()]
        result["file_count"] = len(file_infos) - sum(
            info.filename == "package-manifest.json" for info in file_infos
        )
        result["content_size"] = sum(
            info.file_size for info in file_infos if info.filename != "package-manifest.json"
        )
        if len(file_infos) > MAX_PACKAGE_FILES + 1:
            errors.append(f"提交包文件数量超过上限（{MAX_PACKAGE_FILES} 个内容文件）。")
            return result
        if sum(info.file_size for info in file_infos) > MAX_PACKAGE_UNCOMPRESSED_BYTES:
            errors.append("提交包解压后大小超过上限（512 MB）。")
            return result

        entries: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = info.filename.rstrip("/")
            if not _is_safe_package_path(name):
                errors.append(f"提交包包含不安全路径：{info.filename}")
                continue
            if name in entries:
                errors.append(f"提交包包含重复路径：{name}")
                continue
            if info.flag_bits & 0x1:
                errors.append(f"提交包包含加密文件：{name}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                errors.append(f"提交包包含符号链接：{name}")
            entries[name] = info

        manifest_info = entries.get("package-manifest.json")
        if manifest_info is None or manifest_info.is_dir():
            errors.append("提交包缺少 package-manifest.json。")
            return result
        if manifest_info.file_size > MAX_PACKAGE_MANIFEST_BYTES:
            errors.append("package-manifest.json 超过大小上限（1 MB）。")
            return result

        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError, zipfile.BadZipFile):
            errors.append("package-manifest.json 无法解析。")
            return result
        if not isinstance(manifest, dict):
            errors.append("package-manifest.json 顶层结构无效。")
            return result
        result["manifest"] = manifest

        if (
            manifest.get("format") != PACKAGE_FORMAT
            or manifest.get("format_version") != PACKAGE_FORMAT_VERSION
        ):
            errors.append("提交包格式或版本不受支持。")

        records = manifest.get("files")
        if not isinstance(records, list):
            errors.append("提交包文件清单结构无效。")
            return result

        expected: dict[str, dict] = {}
        for record in records:
            if not isinstance(record, dict):
                errors.append("提交包文件清单包含无效记录。")
                continue
            relative = record.get("path")
            size = record.get("size")
            checksum = record.get("sha256")
            if (
                not isinstance(relative, str)
                or not _is_safe_package_path(relative)
                or relative == "package-manifest.json"
                or not isinstance(size, int)
                or size < 0
                or not isinstance(checksum, str)
                or not _SHA256_PATTERN.fullmatch(checksum)
            ):
                errors.append("提交包文件清单包含格式错误的记录。")
                continue
            if relative in expected:
                errors.append(f"提交包文件清单包含重复文件：{relative}")
                continue
            expected[relative] = record

        actual = {
            name: info
            for name, info in entries.items()
            if not info.is_dir() and name != "package-manifest.json"
        }
        missing = sorted(set(expected) - set(actual))
        unlisted = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"文件清单中的内容缺失：{'、'.join(missing[:5])}")
        if unlisted:
            errors.append(f"提交包包含未登记文件：{'、'.join(unlisted[:5])}")

        verified_files: list[dict] = result["files"]
        for relative, record in expected.items():
            info = actual.get(relative)
            file_result = {
                "path": relative,
                "size": record["size"],
                "sha256": record["sha256"],
                "verified": False,
            }
            verified_files.append(file_result)
            if info is None:
                continue
            try:
                content = archive.read(info)
            except (RuntimeError, zipfile.BadZipFile):
                errors.append(f"文件无法解压或 CRC 校验失败：{relative}")
                continue
            actual_checksum = _sha256_bytes(content)
            if len(content) != record["size"]:
                errors.append(f"文件大小校验失败：{relative}")
                continue
            if actual_checksum != record["sha256"]:
                errors.append(f"文件 SHA-256 校验失败：{relative}")
                continue
            file_result["verified"] = True

        required_files = {"最终提交材料清单.csv", "提交包说明.txt"}
        for required in sorted(required_files - set(actual)):
            errors.append(f"提交包缺少必要文件：{required}")

        word_paths = sorted(name for name in actual if name.startswith("投标文件/"))
        word_metadata = manifest.get("word")
        if len(word_paths) != 1:
            errors.append("提交包必须且只能包含一个投标 Word 文件。")
        elif not word_paths[0].lower().endswith(".docx"):
            errors.append("提交包中的投标文件不是 DOCX。")
        elif not isinstance(word_metadata, dict):
            errors.append("提交包缺少 Word 来源信息。")
        else:
            word_record = expected.get(word_paths[0], {})
            if word_metadata.get("filename") != Path(word_paths[0]).name:
                errors.append("Word 来源文件名与包内文件不一致。")
            if word_metadata.get("sha256") != word_record.get("sha256"):
                errors.append("Word 来源 SHA-256 与文件清单不一致。")

        checklist_info = actual.get("最终提交材料清单.csv")
        if checklist_info is not None:
            try:
                if not archive.read(checklist_info).startswith(b"\xef\xbb\xbf"):
                    errors.append("最终提交材料清单.csv 缺少 UTF-8 BOM。")
            except (RuntimeError, zipfile.BadZipFile):
                pass

        checklist_summary = manifest.get("checklist")
        if not isinstance(checklist_summary, dict):
            errors.append("提交包缺少材料清单摘要。")
        else:
            pending_required = checklist_summary.get("pending_required", 0)
            broken_links = checklist_summary.get("broken_links", 0)
            if (
                not isinstance(checklist_summary.get("complete"), bool)
                or not _is_nonnegative_int(pending_required)
                or not _is_nonnegative_int(broken_links)
            ):
                errors.append("提交包材料清单摘要格式无效。")
            elif not checklist_summary["complete"] or pending_required > 0 or broken_links > 0:
                errors.append("提交包材料清单摘要未达到可打包状态。")

        internal_review_only = manifest.get("internal_review_only")
        review_summary = manifest.get("review")
        if not isinstance(internal_review_only, bool):
            errors.append("提交包用途标记无效。")
        elif internal_review_only:
            warnings.append("该提交包标记为仅供内部预审，不可直接作为最终递交依据。")
        if isinstance(review_summary, dict):
            pending_review = review_summary.get("pending", 0)
            if not _is_nonnegative_int(pending_review):
                errors.append("提交包复核摘要格式无效。")
            elif pending_review > 0 and internal_review_only is False:
                errors.append("存在待处理复核问题，但提交包未标记为内部预审。")
        else:
            errors.append("提交包缺少复核摘要。")

    result["valid"] = not errors
    return result


def build_package_verification_report(
    verification: dict,
    *,
    package_version: dict | None = None,
) -> bytes:
    conclusion = "通过" if verification.get("valid") else "不通过"
    lines = [
        "提交包完整性校验报告",
        "=" * 28,
        f"校验结论：{conclusion}",
        f"校验时间：{verification.get('verified_at', '-')}",
        f"文件名称：{verification.get('filename', '-')}",
        f"文件大小：{int(verification.get('size', 0))} 字节",
        f"实际 SHA-256：{verification.get('sha256', '-')}",
        f"记录 SHA-256：{verification.get('expected_sha256') or '-'}",
        f"内容文件数：{int(verification.get('file_count', 0))}",
        f"内容总大小：{int(verification.get('content_size', 0))} 字节",
    ]
    if package_version:
        lines.extend(
            [
                f"提交包版本：P{int(package_version.get('version', 0)):03d}",
                f"Word 来源：V{int(package_version.get('word_version', 0)):03d} "
                f"{package_version.get('word_filename', '')}",
                f"版本说明：{package_version.get('note') or '-'}",
            ]
        )

    lines.extend(["", "错误项："])
    errors = verification.get("errors") or []
    lines.extend(f"- {item}" for item in errors)
    if not errors:
        lines.append("- 无")

    lines.extend(["", "提示项："])
    warnings = verification.get("warnings") or []
    lines.extend(f"- {item}" for item in warnings)
    if not warnings:
        lines.append("- 无")

    lines.extend(["", "文件校验明细："])
    files = verification.get("files") or []
    for item in files:
        status_text = "通过" if item.get("verified") else "失败"
        lines.append(
            f"- [{status_text}] {item.get('path', '-')} | "
            f"{int(item.get('size', 0))} 字节 | {item.get('sha256', '-')}"
        )
    if not files:
        lines.append("- 无可用明细")

    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")


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
