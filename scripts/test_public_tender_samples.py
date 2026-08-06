from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bid_assistant.analyzer import analyze_with_rules
from bid_assistant.parsers import parse_document


PUBLIC_SAMPLE_EXTENSIONS = {".pdf", ".docx"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_sample(path: Path) -> dict:
    parsed = parse_document(path)
    analysis = analyze_with_rules(parsed)
    info = analysis.project_info
    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "file_type": parsed.file_type,
        "page_count": len(parsed.pages),
        "character_count": parsed.char_count,
        "parser_engine": parsed.parser_engine,
        "possible_scanned_document": parsed.possible_scanned_document,
        "parser_warnings": parsed.warnings,
        "project_info": info.model_dump(),
        "analysis_counts": {
            "mandatory_requirements": len(analysis.mandatory_requirements),
            "scoring_items": len(analysis.scoring_items),
            "qualification_requirements": len(analysis.qualification_requirements),
            "required_documents": len(analysis.required_documents),
            "deadlines": len(analysis.deadlines),
            "risks": len(analysis.risks),
        },
    }


def _markdown_report(generated_at: str, source_dir: Path, records: list[dict]) -> str:
    lines = [
        "# 公开招标文件回归测试报告",
        "",
        f"- 生成时间（北京时间）：{generated_at}",
        f"- 测试目录：`{source_dir.resolve()}`",
        f"- 文件数量：{len(records)}",
        "",
        "## 解析结果",
        "",
        "| 文件 | 页数 | 字符数 | 引擎 | 扫描件风险 | 项目名称 | 招标人/采购人 |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for item in records:
        info = item["project_info"]
        lines.append(
            "| {filename} | {pages} | {chars} | {engine} | {scan} | {project} | {purchaser} |".format(
                filename=item["filename"].replace("|", "\\|"),
                pages=item["page_count"],
                chars=item["character_count"],
                engine=item["parser_engine"],
                scan="是" if item["possible_scanned_document"] else "否",
                project=(info.get("project_name") or "未提取").replace("|", "\\|"),
                purchaser=(info.get("purchaser") or "未提取").replace("|", "\\|"),
            )
        )

    lines.extend(["", "## 详细记录", ""])
    for item in records:
        info = item["project_info"]
        counts = item["analysis_counts"]
        lines.extend(
            [
                f"### {item['filename']}",
                "",
                f"- 文件大小：{item['size_bytes']} 字节",
                f"- SHA-256：`{item['sha256']}`",
                f"- 项目名称：{info.get('project_name') or '未提取'}",
                f"- 招标人/采购人：{info.get('purchaser') or '未提取'}",
                f"- 代理机构：{info.get('agency') or '未提取'}",
                f"- 预算或控制价：{info.get('budget') or '未提取'}",
                f"- 投标截止时间：{info.get('bid_deadline') or '未提取'}",
                f"- 强制要求：{counts['mandatory_requirements']} 条",
                f"- 评分项：{counts['scoring_items']} 条",
                f"- 资格要求：{counts['qualification_requirements']} 条",
                f"- 所需材料：{counts['required_documents']} 条",
                f"- 时间节点：{counts['deadlines']} 条",
                f"- 废标风险：{counts['risks']} 条",
                "- 解析警告：" + ("；".join(item["parser_warnings"]) or "无"),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="批量验证公开招标文件的解析与规则分析结果。")
    parser.add_argument("source_dir", type=Path, help="公开测试文件所在目录。")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/public_sample_reports"),
        help="Markdown 和 JSON 报告输出目录。",
    )
    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    candidates = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in PUBLIC_SAMPLE_EXTENSIONS
    )
    if not candidates:
        raise SystemExit(f"没有找到支持的招标文件：{source_dir}")

    records = [inspect_sample(path) for path in candidates]
    generated_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    payload = {
        "generated_at_beijing": generated_at,
        "source_dir": str(source_dir),
        "records": records,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "公开招标文件回归测试.json"
    markdown_path = args.output_dir / "公开招标文件回归测试.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(_markdown_report(generated_at, source_dir, records), encoding="utf-8")
    print(markdown_path.resolve())
    print(json_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
