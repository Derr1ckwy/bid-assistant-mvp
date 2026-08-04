from __future__ import annotations

from datetime import datetime, timezone

from bid_assistant.models import RequirementItem, ScoringItem, TenderAnalysis


_REQUIREMENT_GROUPS = (
    ("强制要求", "mandatory_requirements"),
    ("资格要求", "qualification_requirements"),
    ("所需材料", "required_documents"),
    ("时间节点", "deadlines"),
    ("废标风险", "risks"),
)


def _requirement_row(category: str, item: RequirementItem) -> dict:
    return {
        "id": item.id,
        "category": category,
        "content": item.content.strip(),
        "source_page": item.source_page,
        "source_quote": item.source_quote.strip(),
        "confidence": item.confidence,
        "status": item.status,
    }


def _scoring_row(item: ScoringItem) -> dict:
    return {
        "id": item.id,
        "category": "评分项",
        "content": item.criterion.strip(),
        "source_page": item.source_page,
        "source_quote": item.source_quote.strip(),
        "confidence": item.confidence,
        "status": item.status,
    }


def _analysis_rows(analysis: TenderAnalysis) -> list[dict]:
    rows: list[dict] = []
    for category, field_name in _REQUIREMENT_GROUPS:
        rows.extend(_requirement_row(category, item) for item in getattr(analysis, field_name))
    rows.extend(_scoring_row(item) for item in analysis.scoring_items)
    return rows


def _changed_from_baseline(baseline: dict, current: dict) -> bool:
    return any(
        baseline.get(field) != current.get(field)
        for field in ("content", "source_page", "source_quote")
    )


def _metric_percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def build_analysis_acceptance(
    baseline: TenderAnalysis,
    current: TenderAnalysis,
    *,
    baseline_origin: str = "自动分析",
    source_matches: bool | None = None,
    source_filename: str = "",
    reviewer: str = "",
    manual_minutes: float = 0,
    assisted_minutes: float = 0,
    notes: str = "",
) -> dict:
    baseline_rows = _analysis_rows(baseline)
    current_rows = _analysis_rows(current)
    baseline_by_id = {item["id"]: item for item in baseline_rows}
    current_by_id = {item["id"]: item for item in current_rows}

    accepted_items: list[dict] = []
    rejected_items: list[dict] = []
    pending_items: list[dict] = []
    manual_items: list[dict] = []
    edited_count = 0

    for item_id, baseline_item in baseline_by_id.items():
        current_item = current_by_id.get(item_id)
        if current_item is None:
            rejected_items.append({**baseline_item, "result": "人工删除"})
            continue
        if _changed_from_baseline(baseline_item, current_item):
            edited_count += 1
        if current_item["status"] == "已确认":
            accepted_items.append({**current_item, "result": "确认命中"})
        elif current_item["status"] == "忽略":
            rejected_items.append({**current_item, "result": "标记忽略"})
        else:
            pending_items.append({**current_item, "result": current_item["status"]})

    for item_id, current_item in current_by_id.items():
        if item_id in baseline_by_id or current_item["status"] == "忽略":
            continue
        result = "人工补充并确认" if current_item["status"] == "已确认" else "人工补充待确认"
        manual_item = {**current_item, "result": result}
        manual_items.append(manual_item)

    accepted_count = len(accepted_items)
    rejected_count = len(rejected_items)
    manual_addition_count = len(manual_items)
    confirmed_manual_count = sum(item["status"] == "已确认" for item in manual_items)
    manual_pending_count = manual_addition_count - confirmed_manual_count
    pending_count = len(pending_items) + manual_pending_count
    reviewed_count = accepted_count + rejected_count
    complete = bool(baseline_rows) and pending_count == 0 and source_matches is not False
    hit_rate = _metric_percent(accepted_count, reviewed_count)
    coverage = (
        _metric_percent(accepted_count, accepted_count + confirmed_manual_count)
        if complete
        else None
    )

    manual_minutes = round(max(float(manual_minutes), 0), 1)
    assisted_minutes = round(max(float(assisted_minutes), 0), 1)
    time_saved = None
    time_saving_percent = None
    if manual_minutes > 0 and assisted_minutes > 0:
        time_saved = round(manual_minutes - assisted_minutes, 1)
        time_saving_percent = round(time_saved / manual_minutes * 100, 1)

    baseline_origin = baseline_origin.strip()[:100] or "未知"
    warnings: list[str] = []
    if baseline_origin != "自动分析":
        warnings.append("该基线由历史项目当前结果建立，只统计建基线之后的变化。")
    if source_matches is False:
        warnings.append("当前解析结果与验收基线来源不一致，请重新分析后再记录验收结果。")
    if not baseline_rows:
        warnings.append("自动分析基线为空，无法形成有效验收样本。")
    if pending_count:
        warnings.append(f"仍有 {pending_count} 项待确认或待核对，覆盖率暂不计算。")
    if manual_minutes <= 0 or assisted_minutes <= 0:
        warnings.append("尚未完整填写纯人工耗时和系统协助耗时。")
    if not reviewer.strip():
        warnings.append("尚未填写验收人。")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "source_filename": source_filename.strip(),
        "analysis_mode": current.analysis_mode,
        "baseline_origin": baseline_origin,
        "source_matches": source_matches,
        "reviewer": reviewer.strip()[:100],
        "manual_minutes": manual_minutes,
        "assisted_minutes": assisted_minutes,
        "time_saved_minutes": time_saved,
        "time_saving_percent": time_saving_percent,
        "notes": notes.strip()[:2000],
        "complete": complete,
        "baseline_count": len(baseline_rows),
        "current_count": len(current_rows),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "pending_count": pending_count,
        "manual_addition_count": manual_addition_count,
        "confirmed_manual_count": confirmed_manual_count,
        "manual_pending_count": manual_pending_count,
        "edited_count": edited_count,
        "reviewed_hit_rate_percent": hit_rate,
        "estimated_coverage_percent": coverage,
        "accepted_items": accepted_items,
        "rejected_items": rejected_items,
        "pending_items": pending_items,
        "manual_items": manual_items,
        "warnings": warnings,
    }


def _percent_text(value: float | None) -> str:
    return "待完成复核" if value is None else f"{value:.1f}%"


def _minutes_text(value: float | None) -> str:
    return "未填写" if value is None or value <= 0 else f"{value:.1f} 分钟"


def build_acceptance_report(record: dict) -> bytes:
    conclusion = "完整样本" if record.get("complete") else "阶段性样本"
    lines = [
        "真实招标项目分析验收报告",
        "=" * 32,
        f"验收状态：{conclusion}",
        f"生成时间：{record.get('generated_at', '-')}",
        f"源文件：{record.get('source_filename') or '-'}",
        f"分析模式：{record.get('analysis_mode') or '-'}",
        f"基线来源：{record.get('baseline_origin') or '-'}",
        "源文件一致性："
        + {True: "一致", False: "不一致", None: "未记录"}.get(record.get("source_matches"), "未记录"),
        f"验收人：{record.get('reviewer') or '-'}",
        "",
        "指标汇总：",
        f"- 自动提取基线：{int(record.get('baseline_count', 0))} 项",
        f"- 人工确认命中：{int(record.get('accepted_count', 0))} 项",
        f"- 误报或删除：{int(record.get('rejected_count', 0))} 项",
        f"- 人工补充漏项：{int(record.get('manual_addition_count', 0))} 项",
        f"- 人工修改原提取项：{int(record.get('edited_count', 0))} 项",
        f"- 待确认或待核对：{int(record.get('pending_count', 0))} 项",
        f"- 已审核项命中率：{_percent_text(record.get('reviewed_hit_rate_percent'))}",
        f"- 覆盖率估算：{_percent_text(record.get('estimated_coverage_percent'))}",
        "",
        "耗时记录：",
        f"- 纯人工预计耗时：{_minutes_text(record.get('manual_minutes'))}",
        f"- 系统协助实际耗时：{_minutes_text(record.get('assisted_minutes'))}",
        f"- 节省时间：{_minutes_text(record.get('time_saved_minutes'))}",
        f"- 节省比例：{_percent_text(record.get('time_saving_percent'))}",
    ]

    for title, key in (
        ("人工确认命中项", "accepted_items"),
        ("误报或删除项", "rejected_items"),
        ("人工补充漏项", "manual_items"),
        ("待确认项", "pending_items"),
    ):
        lines.extend(["", f"{title}："])
        items = record.get(key) or []
        if not items:
            lines.append("- 无")
            continue
        for item in items:
            page = f"第 {item['source_page']} 页" if item.get("source_page") else "页码未知"
            lines.append(
                f"- [{item.get('category', '-')}/{item.get('result', '-')}/{page}] "
                f"{item.get('content', '-')}"
            )

    lines.extend(["", "验收提示："])
    warnings = record.get("warnings") or []
    lines.extend(f"- {item}" for item in warnings)
    if not warnings:
        lines.append("- 无")

    lines.extend(
        [
            "",
            "人工备注：",
            record.get("notes") or "无",
            "",
            "说明：命中率和覆盖率基于人工确认结果估算，用于比较项目迭代效果，不替代正式统计学评测。",
        ]
    )
    return ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")
