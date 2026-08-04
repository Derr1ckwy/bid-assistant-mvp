from __future__ import annotations

import re
from collections import Counter

from bid_assistant.models import ChapterDraft, ReviewIssue, ReviewReport, TenderAnalysis


PLACEHOLDER_PATTERN = re.compile(r"待补充|待确认|待核对|暂无已确认|页码未知")
CONSISTENCY_PATTERNS = {
    "工期/交付期": re.compile(
        r"(?:工期|交付期|建设周期|服务期限)[^。；\n]{0,24}?(\d+\s*(?:个)?(?:工作日|日|天|个月|月|年))"
    ),
    "质保期": re.compile(r"(?:质保期|保修期)[^。；\n]{0,24}?(\d+\s*(?:个)?(?:工作日|日|天|个月|月|年))"),
}


def _issue(
    severity: str,
    category: str,
    message: str,
    suggestion: str,
    *,
    related_id: str = "",
    source_page: int | None = None,
) -> ReviewIssue:
    return ReviewIssue(
        severity=severity,
        category=category,
        message=message,
        suggestion=suggestion,
        related_id=related_id,
        source_page=source_page,
    )


def build_review_report(analysis: TenderAnalysis, drafts: list[ChapterDraft]) -> ReviewReport:
    issues: list[ReviewIssue] = []
    project_fields = (
        ("项目名称", analysis.project_info.project_name, "高"),
        ("招标人/采购人", analysis.project_info.purchaser, "高"),
        ("预算/最高限价", analysis.project_info.budget, "中"),
        ("投标截止时间", analysis.project_info.bid_deadline, "高"),
        ("代理机构", analysis.project_info.agency, "低"),
    )
    for label, value, severity in project_fields:
        if not value.strip():
            issues.append(
                _issue(
                    severity,
                    "项目信息",
                    f"{label}尚未确认。",
                    "返回分析确认页，根据招标文件原文补充并保存。",
                )
            )

    for warning in analysis.warnings:
        if warning.startswith("LLM 已分"):
            continue
        severity = "高" if "扫描件" in warning or "没有提取" in warning else "中"
        issues.append(_issue(severity, "解析告警", warning, "检查源文件解析质量，必要时更换文本版文件或接入 OCR。"))

    requirement_groups = (
        ("强制要求", analysis.mandatory_requirements, "高"),
        ("资格要求", analysis.qualification_requirements, "中"),
        ("所需材料", analysis.required_documents, "中"),
        ("时间节点", analysis.deadlines, "高"),
    )
    for category, items, severity in requirement_groups:
        for item in items:
            if item.status != "已确认":
                action = "恢复并确认" if item.status == "忽略" else "对照原文确认"
                issues.append(
                    _issue(
                        severity,
                        category,
                        f"[{item.status}] {item.content}",
                        f"{action}该条目，确认是否需要写入投标响应。",
                        related_id=item.id,
                        source_page=item.source_page,
                    )
                )
            if not item.source_quote.strip():
                issues.append(
                    _issue(
                        "中",
                        "原文追溯",
                        f"{category}缺少原文引用：{item.content}",
                        "补充招标文件原文和页码，避免无法追溯。",
                        related_id=item.id,
                        source_page=item.source_page,
                    )
                )
            elif item.source_page is None:
                issues.append(
                    _issue(
                        "低",
                        "原文追溯",
                        f"{category}已有原文引用但缺少页码：{item.content}",
                        "补充准确页码，便于投标负责人快速回查。",
                        related_id=item.id,
                    )
                )

    for item in analysis.scoring_items:
        if item.status != "已确认":
            issues.append(
                _issue(
                    "中",
                    "评分项",
                    f"[{item.status}] {item.criterion}",
                    "确认评分标准，并检查章节目录是否有对应响应。",
                    related_id=item.id,
                    source_page=item.source_page,
                )
            )
        if not item.points.strip():
            issues.append(
                _issue(
                    "低",
                    "评分项",
                    f"评分项未识别到分值：{item.criterion}",
                    "对照评分表补充分值，便于确定撰写优先级。",
                    related_id=item.id,
                    source_page=item.source_page,
                )
            )
        if not item.source_quote.strip():
            issues.append(
                _issue(
                    "中",
                    "原文追溯",
                    f"评分项缺少原文引用：{item.criterion}",
                    "补充评分表原文和页码。",
                    related_id=item.id,
                    source_page=item.source_page,
                )
            )
        elif item.source_page is None:
            issues.append(
                _issue(
                    "低",
                    "原文追溯",
                    f"评分项已有原文引用但缺少页码：{item.criterion}",
                    "补充评分表所在页码。",
                    related_id=item.id,
                )
            )

    for item in analysis.risks:
        if item.status != "忽略":
            issues.append(
                _issue(
                    "高",
                    "废标风险",
                    item.content,
                    "由投标负责人逐条确认，并在提交前完成证据检查。",
                    related_id=item.id,
                    source_page=item.source_page,
                )
            )

    selected_plans = [item for item in analysis.outline if item.selected]
    drafts_by_id = {item.chapter_id: item for item in drafts}
    for plan in selected_plans:
        if plan.id not in drafts_by_id:
            issues.append(
                _issue(
                    "高",
                    "章节完整性",
                    f"所选章节尚未生成：{plan.title}",
                    "返回章节生成页生成或取消选择该章节。",
                    related_id=plan.id,
                )
            )

    title_counts = Counter(item.title.strip() for item in drafts if item.title.strip())
    for title, count in title_counts.items():
        if count > 1:
            issues.append(_issue("低", "章节完整性", f"存在 {count} 个同名章节：{title}", "合并或重命名重复章节。"))

    for draft in drafts:
        placeholders = PLACEHOLDER_PATTERN.findall(draft.markdown)
        if placeholders:
            issues.append(
                _issue(
                    "中",
                    "正文占位符",
                    f"《{draft.title}》仍有 {len(placeholders)} 处待补充或待确认内容。",
                    "逐项补充真实材料，完成后删除占位文字。",
                    related_id=draft.chapter_id,
                )
            )
        if any(keyword in draft.title for keyword in ("资格", "业绩", "产品")) and not draft.evidence_sources:
            issues.append(
                _issue(
                    "中",
                    "资料依据",
                    f"《{draft.title}》没有引用企业知识资料。",
                    "上传并引用真实企业、产品或历史项目材料。",
                    related_id=draft.chapter_id,
                )
            )

    for label, pattern in CONSISTENCY_PATTERNS.items():
        values: dict[str, set[str]] = {}
        for draft in drafts:
            for match in pattern.finditer(draft.markdown):
                value = re.sub(r"\s+", "", match.group(1))
                values.setdefault(value, set()).add(draft.title)
        if len(values) > 1:
            detail = "；".join(f"{value}（{'、'.join(sorted(titles))}）" for value, titles in sorted(values.items()))
            issues.append(
                _issue(
                    "高",
                    "跨章节一致性",
                    f"{label}出现不一致值：{detail}",
                    "回查招标文件和实施计划，统一所有章节中的承诺值。",
                )
            )

    if not drafts:
        issues.append(_issue("高", "章节完整性", "尚未生成任何章节草稿。", "完成章节计划并生成至少一个章节。"))

    return ReviewReport(issues=issues)
