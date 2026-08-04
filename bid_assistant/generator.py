from __future__ import annotations

from bid_assistant.llm import LLMError, OpenAICompatibleClient
from bid_assistant.models import ChapterDraft, ChapterPlan, KnowledgeChunk, TenderAnalysis


def _requirements_for_chapter(plan: ChapterPlan, analysis: TenderAnalysis) -> list[str]:
    items = [
        *analysis.mandatory_requirements,
        *analysis.qualification_requirements,
        *analysis.required_documents,
    ]
    by_id = {item.id: item.content for item in items}
    scoring_by_id = {item.id: item.criterion for item in analysis.scoring_items}
    selected = [by_id[item_id] for item_id in plan.requirement_ids if item_id in by_id]
    selected.extend(scoring_by_id[item_id] for item_id in plan.requirement_ids if item_id in scoring_by_id)
    return selected[:30]


def _prompt(plan: ChapterPlan, analysis: TenderAnalysis, evidence: list[KnowledgeChunk]) -> str:
    requirements = _requirements_for_chapter(plan, analysis)
    requirement_text = "\n".join(f"- {item}" for item in requirements) or "- 暂无已确认的直接要求"
    evidence_text = "\n\n".join(
        f"[资料{index}] 来源：{item.source_file}；类别：{item.category}\n{item.text}"
        for index, item in enumerate(evidence, start=1)
    ) or "（没有检索到企业资料。涉及企业事实时必须写‘待补充’，不得自行编造。）"
    project = analysis.project_info
    return f"""你是企业投标文件撰写助手，请生成章节《{plan.title}》的 Markdown 初稿。

项目名称：{project.project_name or '待确认'}
招标人：{project.purchaser or '待确认'}
章节要求：{plan.instructions}

相关招标要求：
{requirement_text}

企业知识资料：
{evidence_text}

输出规则：
1. 从二级标题开始，不生成封面和目录。
2. 对招标要求逐条响应，内容要可执行。
3. 企业资质、人员、案例、参数、金额只能来自资料；没有资料时写“待补充”。
4. 使用资料时标注 [资料1] 这样的引用。
5. 不声称这是最终可提交标书。
"""


def _fallback_markdown(plan: ChapterPlan, analysis: TenderAnalysis, evidence: list[KnowledgeChunk]) -> str:
    requirements = _requirements_for_chapter(plan, analysis)
    lines = [
        "## 响应目标",
        "",
        f"本章围绕“{plan.title}”形成投标初稿，所有项目事实和企业能力均需在提交前人工复核。",
        "",
        "## 招标要求对应",
        "",
    ]
    if requirements:
        lines.extend(f"- {item}" for item in requirements)
    else:
        lines.append("- 暂未关联明确要求，请在分析确认页补充。")

    lines.extend(["", "## 响应方案", ""])
    if evidence:
        for index, item in enumerate(evidence, start=1):
            excerpt = item.text[:500].strip()
            lines.extend(
                [
                    f"### 资料依据 {index}",
                    "",
                    f"根据《{item.source_file}》中的资料，本章可引用以下内容：[资料{index}]",
                    "",
                    excerpt,
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "当前未检索到可引用的企业资料。以下信息必须由投标人员补充：",
                "",
                "- 对应产品或服务能力；",
                "- 可证明的项目案例；",
                "- 实施人员与资质材料；",
                "- 可量化的服务承诺。",
                "",
            ]
        )

    lines.extend(
        [
            "## 实施与保障措施",
            "",
            "1. 建立需求、任务、责任人和交付物对应清单。",
            "2. 对关键节点实施阶段检查，问题形成闭环记录。",
            "3. 对金额、日期、人员、证书及项目名称进行跨章节一致性复核。",
            "4. 在提交前依据招标文件逐条完成合规确认。",
            "",
            "## 待确认事项",
            "",
            "- 待确认本章与评分项的完整对应关系。",
            "- 待补充企业真实证明资料和附件索引。",
        ]
    )
    return "\n".join(lines)


def generate_chapter(
    plan: ChapterPlan,
    analysis: TenderAnalysis,
    evidence: list[KnowledgeChunk],
    client: OpenAICompatibleClient | None = None,
    *,
    use_llm: bool = False,
) -> ChapterDraft:
    markdown = ""
    if use_llm and client is not None:
        try:
            markdown = client.chat([{"role": "user", "content": _prompt(plan, analysis, evidence)}])
        except LLMError:
            markdown = ""
    if not markdown.strip():
        markdown = _fallback_markdown(plan, analysis, evidence)
    return ChapterDraft(
        chapter_id=plan.id,
        title=plan.title,
        markdown=markdown.strip(),
        evidence_sources=list(dict.fromkeys(item.source_file for item in evidence)),
    )
