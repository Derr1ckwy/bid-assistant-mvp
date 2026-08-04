from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import ValidationError

from bid_assistant.llm import LLMError, OpenAICompatibleClient
from bid_assistant.models import (
    ChapterPlan,
    ParsedDocument,
    ProjectInfo,
    RequirementItem,
    ScoringItem,
    TenderAnalysis,
)


MANDATORY_WORDS = ("必须", "应当", "不得", "须", "实质性", "无效投标", "否决投标", "★", "▲")
QUALIFICATION_WORDS = ("资质", "资格", "证书", "业绩", "项目经理", "注册资本", "人员要求")
SCORING_WORDS = ("评分", "分值", "得分", "满分", "评审标准", "评分标准")
DOCUMENT_WORDS = ("提供", "提交", "附", "证明材料", "复印件", "扫描件", "承诺函", "授权书")
RISK_WORDS = ("无效投标", "否决投标", "被否决", "否决", "不予受理", "废标", "重大偏差", "实质性偏离")
DEADLINE_WORDS = ("截止时间", "投标截止", "递交截止", "有效期", "服务期限", "工期")
LLM_CHUNK_CHARS = 45000
LLM_MAX_CHUNKS = 8


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t-•·")


def _iter_lines(document: ParsedDocument) -> Iterable[tuple[int | None, str]]:
    for page in document.pages:
        for raw_line in page.text.splitlines():
            line = _clean_line(raw_line)
            if 6 <= len(line) <= 500:
                yield page.page_number, line


def _dedupe_requirements(items: list[RequirementItem], limit: int = 80) -> list[RequirementItem]:
    result: list[RequirementItem] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"[^\w\u4e00-\u9fff]", "", item.content).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _extract_project_info(text: str) -> ProjectInfo:
    def match(patterns: list[str]) -> str:
        for pattern in patterns:
            found = re.search(pattern, text, flags=re.IGNORECASE)
            if found:
                return _clean_line(found.group(1))[:200]
        return ""

    return ProjectInfo(
        project_name=match([r"项目名称\s*[：:]\s*([^\n]{2,120})", r"采购项目\s*[：:]\s*([^\n]{2,120})"]),
        purchaser=match([r"(?:采购人|招标人|招标单位)\s*[：:]\s*([^\n]{2,120})"]),
        agency=match([r"(?:代理机构|采购代理机构)\s*[：:]\s*([^\n]{2,120})"]),
        budget=match([r"(?:预算金额|最高限价|采购预算)\s*[：:]\s*([^\n]{2,80})"]),
        bid_deadline=match([r"(?:投标截止时间|递交截止时间|响应文件提交截止时间)\s*[：:]\s*([^\n]{2,100})"]),
    )


def analyze_with_rules(document: ParsedDocument) -> TenderAnalysis:
    mandatory: list[RequirementItem] = []
    qualifications: list[RequirementItem] = []
    required_documents: list[RequirementItem] = []
    deadlines: list[RequirementItem] = []
    risks: list[RequirementItem] = []
    scoring: list[ScoringItem] = []

    for page_number, line in _iter_lines(document):
        base = {
            "source_page": page_number,
            "source_quote": line,
            "confidence": 0.65,
        }
        if any(word in line for word in MANDATORY_WORDS):
            mandatory.append(RequirementItem(content=line, category="强制要求", **base))
        if any(word in line for word in QUALIFICATION_WORDS):
            qualifications.append(RequirementItem(content=line, category="资格要求", **base))
        if any(word in line for word in DOCUMENT_WORDS) and any(
            noun in line for noun in ("证书", "文件", "材料", "证明", "复印件", "扫描件", "函")
        ):
            required_documents.append(RequirementItem(content=line, category="所需材料", **base))
        if any(word in line for word in DEADLINE_WORDS):
            deadlines.append(RequirementItem(content=line, category="时间节点", **base))
        if any(word in line for word in RISK_WORDS):
            risks.append(RequirementItem(content=line, category="废标风险", confidence=0.8, source_page=page_number, source_quote=line))
        points_match = re.search(r"(\d+(?:\.\d+)?)\s*分", line)
        if points_match or any(word in line for word in SCORING_WORDS):
            scoring.append(
                ScoringItem(
                    criterion=line,
                    points=points_match.group(1) if points_match else "",
                    response_hint="围绕评分依据逐条提供响应内容和证明材料。",
                    source_page=page_number,
                    source_quote=line,
                    confidence=0.65,
                )
            )

    analysis = TenderAnalysis(
        project_info=_extract_project_info(document.full_text),
        mandatory_requirements=_dedupe_requirements(mandatory),
        scoring_items=_dedupe_scoring(scoring),
        qualification_requirements=_dedupe_requirements(qualifications, limit=50),
        required_documents=_dedupe_requirements(required_documents, limit=50),
        deadlines=_dedupe_requirements(deadlines, limit=30),
        risks=_dedupe_requirements(risks, limit=30),
        analysis_mode="rules",
        warnings=list(document.warnings),
    )
    analysis.outline = build_outline(analysis)
    return analysis


def _dedupe_scoring(items: list[ScoringItem], limit: int = 50) -> list[ScoringItem]:
    result: list[ScoringItem] = []
    seen: set[str] = set()
    for item in items:
        key = re.sub(r"[^\w\u4e00-\u9fff]", "", item.criterion).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def build_outline(analysis: TenderAnalysis) -> list[ChapterPlan]:
    mandatory_ids = [item.id for item in analysis.mandatory_requirements]
    qualification_ids = [item.id for item in analysis.qualification_requirements]
    scoring_ids = [item.id for item in analysis.scoring_items]
    return [
        ChapterPlan(title="投标函及实质性条款响应", instructions="逐条响应关键条款，不填写未经确认的金额、日期和承诺。", requirement_ids=mandatory_ids[:12]),
        ChapterPlan(title="项目理解与需求分析", instructions="说明对项目目标、范围、难点和评分重点的理解。", requirement_ids=scoring_ids[:10]),
        ChapterPlan(title="技术方案", instructions="围绕技术要求和评分标准组织方案、架构、流程及交付物。", requirement_ids=scoring_ids),
        ChapterPlan(title="实施计划与项目管理", instructions="说明阶段、任务、人员职责、质量和进度控制。", requirement_ids=mandatory_ids[12:24]),
        ChapterPlan(title="服务保障与风险控制", instructions="说明服务机制、应急响应、风险预防和持续改进。", requirement_ids=mandatory_ids[24:]),
        ChapterPlan(title="资格、业绩与证明材料", instructions="只引用知识库中真实存在的企业材料，缺失内容必须标注待补充。", requirement_ids=qualification_ids),
    ]


def _split_long_text(text: str, max_chars: int) -> list[str]:
    result: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        boundary = remaining.rfind("\n", int(max_chars * 0.6), max_chars)
        if boundary < 0:
            boundary = max_chars
        result.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        result.append(remaining)
    return result


def _document_chunks(document: ParsedDocument, max_chars: int = LLM_CHUNK_CHARS) -> list[str]:
    pieces: list[str] = []
    for page in document.pages:
        marker = f"[第 {page.page_number} 页]" if page.page_number else "[页码未知]"
        page_text = f"{marker}\n{page.text.strip()}".strip()
        split_pages = _split_long_text(page_text, max_chars)
        for index, piece in enumerate(split_pages):
            if index and page.page_number:
                piece = f"[第 {page.page_number} 页续]\n{piece}"
            pieces.append(piece)
    if not pieces and document.full_text.strip():
        pieces = _split_long_text(document.full_text, max_chars)

    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        candidate = f"{buffer}\n\n{piece}".strip()
        if buffer and len(candidate) > max_chars:
            chunks.append(buffer)
            buffer = piece
        else:
            buffer = candidate
    if buffer:
        chunks.append(buffer)
    return chunks


def _analysis_prompt(text: str, chunk_index: int, chunk_total: int) -> str:
    return f"""你是招标文件分析助手。请严格根据原文提取信息，不得补写原文没有的事实。

当前处理第 {chunk_index}/{chunk_total} 个文档分段。只提取本分段中存在的内容，其他字段保持空值或空数组。

返回一个 JSON 对象，字段必须是：
project_info: project_name, purchaser, agency, budget, bid_deadline
mandatory_requirements: 数组，每项包含 content, source_page, source_quote, confidence, status
scoring_items: 数组，每项包含 criterion, points, response_hint, source_page, source_quote, confidence, status
qualification_requirements: 数组，格式同 mandatory_requirements
required_documents: 数组，格式同 mandatory_requirements
deadlines: 数组，格式同 mandatory_requirements
risks: 数组，格式同 mandatory_requirements

要求：
1. source_quote 必须是招标文件中的短原文。
2. source_page 使用数字；无法确定时为 null。
3. status 固定为“待确认”。
4. 不生成目录，由系统后续处理。

招标文件：
{text}
"""


def _merge_project_info(items: list[ProjectInfo]) -> ProjectInfo:
    merged = ProjectInfo()
    for item in items:
        for field_name in ProjectInfo.model_fields:
            if not getattr(merged, field_name) and getattr(item, field_name):
                setattr(merged, field_name, getattr(item, field_name))
    return merged


def _merge_llm_analyses(items: list[TenderAnalysis], warnings: list[str], mode: str) -> TenderAnalysis:
    analysis = TenderAnalysis(
        project_info=_merge_project_info([item.project_info for item in items]),
        mandatory_requirements=_dedupe_requirements(
            [requirement for item in items for requirement in item.mandatory_requirements]
        ),
        scoring_items=_dedupe_scoring([score for item in items for score in item.scoring_items]),
        qualification_requirements=_dedupe_requirements(
            [requirement for item in items for requirement in item.qualification_requirements], limit=50
        ),
        required_documents=_dedupe_requirements(
            [requirement for item in items for requirement in item.required_documents], limit=50
        ),
        deadlines=_dedupe_requirements([requirement for item in items for requirement in item.deadlines], limit=30),
        risks=_dedupe_requirements([requirement for item in items for requirement in item.risks], limit=30),
        analysis_mode=mode,
        warnings=list(dict.fromkeys(warnings)),
    )
    analysis.outline = build_outline(analysis)
    return analysis


def _validate_source_quotes(analysis: TenderAnalysis, document: ParsedDocument) -> None:
    page_map = {page.page_number: re.sub(r"\s+", "", page.text) for page in document.pages}
    groups = [
        analysis.mandatory_requirements,
        analysis.qualification_requirements,
        analysis.required_documents,
        analysis.deadlines,
        analysis.risks,
    ]
    for group in groups:
        for item in group:
            quote = re.sub(r"\s+", "", item.source_quote)
            page_text = page_map.get(item.source_page, "")
            if quote and page_text and quote not in page_text:
                item.status = "待核对"
                item.confidence = min(item.confidence, 0.4)
    for item in analysis.scoring_items:
        quote = re.sub(r"\s+", "", item.source_quote)
        page_text = page_map.get(item.source_page, "")
        if quote and page_text and quote not in page_text:
            item.status = "待核对"
            item.confidence = min(item.confidence, 0.4)


def analyze_document(
    document: ParsedDocument,
    client: OpenAICompatibleClient | None = None,
    *,
    use_llm: bool = False,
) -> TenderAnalysis:
    if not use_llm or client is None:
        return analyze_with_rules(document)

    try:
        all_chunks = _document_chunks(document)
        if not all_chunks:
            fallback = analyze_with_rules(document)
            fallback.warnings.append("没有可提交给 LLM 的文本，已使用规则模式。")
            return fallback

        chunks = all_chunks[:LLM_MAX_CHUNKS]
        partial_analyses: list[TenderAnalysis] = []
        failed_chunks: list[str] = []
        for index, text in enumerate(chunks, start=1):
            try:
                payload = client.chat_json(
                    [{"role": "user", "content": _analysis_prompt(text, index, len(chunks))}]
                )
                partial_analyses.append(TenderAnalysis.model_validate(payload))
            except (LLMError, ValidationError, TypeError, ValueError) as exc:
                failed_chunks.append(f"第 {index} 段失败：{exc}")

        if not partial_analyses:
            raise LLMError("；".join(failed_chunks) or "全部文档分段均分析失败。")

        warnings = list(document.warnings)
        mode = "llm_chunked" if len(chunks) > 1 else "llm"
        if len(chunks) > 1:
            warnings.append(f"LLM 已分 {len(chunks)} 段分析并合并结果。")
        if len(all_chunks) > LLM_MAX_CHUNKS:
            warnings.append(
                f"文档共 {len(all_chunks)} 个分析分段，本次只处理前 {LLM_MAX_CHUNKS} 段，请人工检查后续内容。"
            )
        if failed_chunks:
            warnings.append("部分 LLM 分段失败，已保留成功结果：" + "；".join(failed_chunks))

        analysis = _merge_llm_analyses(partial_analyses, warnings, mode)
        _validate_source_quotes(analysis, document)
        return analysis
    except (LLMError, ValidationError, TypeError, ValueError) as exc:
        fallback = analyze_with_rules(document)
        fallback.warnings.append(f"LLM 分析失败，已回退规则模式：{exc}")
        return fallback
