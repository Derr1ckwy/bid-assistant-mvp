from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from bid_assistant.analyzer import analyze_document
from bid_assistant.config import settings
from bid_assistant.exporter import export_docx
from bid_assistant.generator import generate_chapter
from bid_assistant.knowledge import search_knowledge
from bid_assistant.llm import OpenAICompatibleClient
from bid_assistant.models import (
    ChapterDraft,
    ChapterPlan,
    ParsedDocument,
    ProjectInfo,
    RequirementItem,
    ScoringItem,
    TenderAnalysis,
)
from bid_assistant.parsers import DocumentParseError, SUPPORTED_EXTENSIONS, parse_document
from bid_assistant.storage import ProjectStore, safe_filename


st.set_page_config(page_title="投标初稿助手", page_icon="📄", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px;}
    h1, h2, h3 {letter-spacing: 0;}
    [data-testid="stMetricValue"] {font-size: 1.4rem;}
    .status-note {border-left: 4px solid #2f5597; padding: .65rem .85rem; background: #f4f7fb;}
    </style>
    """,
    unsafe_allow_html=True,
)


CATEGORY_LABELS = {
    "company": "企业资料",
    "product": "产品资料",
    "history": "历史方案",
}
STATUS_OPTIONS = ["待确认", "已确认", "忽略", "待核对"]


@st.cache_resource
def get_store() -> ProjectStore:
    return ProjectStore(settings.data_dir)


@st.cache_resource
def get_llm_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(settings)


def _clean_value(value, default=""):
    if value is None:
        return default
    try:
        is_missing = pd.isna(value)
    except (TypeError, ValueError):
        is_missing = False
    if isinstance(is_missing, bool) and is_missing:
        return default
    return value


def _requirement_rows(items: list[RequirementItem]) -> list[dict]:
    return [
        {
            "id": item.id,
            "content": item.content,
            "source_page": item.source_page,
            "source_quote": item.source_quote,
            "confidence": item.confidence,
            "status": item.status,
        }
        for item in items
    ]


def _requirement_editor(label: str, items: list[RequirementItem], key: str) -> list[RequirementItem]:
    st.subheader(label)
    edited = st.data_editor(
        _requirement_rows(items),
        key=key,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": None,
            "content": st.column_config.TextColumn("内容", width="large", required=True),
            "source_page": st.column_config.NumberColumn("页码", min_value=1, step=1),
            "source_quote": st.column_config.TextColumn("原文", width="large"),
            "confidence": st.column_config.NumberColumn("置信度", min_value=0.0, max_value=1.0, format="%.2f"),
            "status": st.column_config.SelectboxColumn("状态", options=STATUS_OPTIONS, required=True),
        },
    )
    rows = edited.to_dict("records") if isinstance(edited, pd.DataFrame) else edited
    result: list[RequirementItem] = []
    for row in rows:
        content = str(_clean_value(row.get("content"))).strip()
        if not content:
            continue
        result.append(
            RequirementItem(
                id=str(_clean_value(row.get("id"), "")) or RequirementItem(content=content).id,
                category=label,
                content=content,
                source_page=int(row["source_page"]) if _clean_value(row.get("source_page"), None) else None,
                source_quote=str(_clean_value(row.get("source_quote"))),
                confidence=float(_clean_value(row.get("confidence"), 0.5)),
                status=str(_clean_value(row.get("status"), "待确认")),
            )
        )
    return result


def _scoring_editor(items: list[ScoringItem], key: str) -> list[ScoringItem]:
    st.subheader("评分项")
    rows = [item.model_dump() for item in items]
    edited = st.data_editor(
        rows,
        key=key,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": None,
            "criterion": st.column_config.TextColumn("评分要求", width="large", required=True),
            "points": st.column_config.TextColumn("分值", width="small"),
            "response_hint": st.column_config.TextColumn("响应提示", width="large"),
            "source_page": st.column_config.NumberColumn("页码", min_value=1, step=1),
            "source_quote": st.column_config.TextColumn("原文", width="large"),
            "confidence": st.column_config.NumberColumn("置信度", min_value=0.0, max_value=1.0, format="%.2f"),
            "status": st.column_config.SelectboxColumn("状态", options=STATUS_OPTIONS, required=True),
        },
    )
    records = edited.to_dict("records") if isinstance(edited, pd.DataFrame) else edited
    result: list[ScoringItem] = []
    for row in records:
        criterion = str(_clean_value(row.get("criterion"))).strip()
        if not criterion:
            continue
        result.append(
            ScoringItem(
                id=str(_clean_value(row.get("id"), "")) or ScoringItem(criterion=criterion).id,
                criterion=criterion,
                points=str(_clean_value(row.get("points"))),
                response_hint=str(_clean_value(row.get("response_hint"))),
                source_page=int(row["source_page"]) if _clean_value(row.get("source_page"), None) else None,
                source_quote=str(_clean_value(row.get("source_quote"))),
                confidence=float(_clean_value(row.get("confidence"), 0.5)),
                status=str(_clean_value(row.get("status"), "待确认")),
            )
        )
    return result


def _load_analysis(store: ProjectStore, project_id: str) -> TenderAnalysis | None:
    payload = store.load_json(project_id, "analysis")
    return TenderAnalysis.model_validate(payload) if payload else None


store = get_store()
llm_client = get_llm_client()
projects = store.list_projects()

st.sidebar.title("投标项目")
new_name = st.sidebar.text_input("新项目名称", placeholder="例如：某某信息化项目")
if st.sidebar.button("新建项目", type="primary", use_container_width=True):
    project = store.create_project(new_name)
    st.session_state["project_id"] = project["id"]
    st.rerun()

project_ids = [project["id"] for project in projects]
project_names = {project["id"]: project["name"] for project in projects}
current_id = st.session_state.get("project_id")
if current_id not in project_ids:
    current_id = project_ids[0] if project_ids else None

if project_ids:
    selected_id = st.sidebar.selectbox(
        "当前项目",
        project_ids,
        index=project_ids.index(current_id) if current_id in project_ids else 0,
        format_func=lambda project_id: project_names[project_id],
    )
    st.session_state["project_id"] = selected_id
    current_id = selected_id

with st.sidebar.expander("模型配置", expanded=False):
    st.code(f"{settings.llm_model}\n{settings.llm_base_url}")
    if st.button("检测模型连接", use_container_width=True):
        if llm_client.is_available():
            st.success("模型接口可用")
        else:
            st.warning("模型不可用，系统仍可使用规则模式")

st.sidebar.caption("当前版本只生成可复核初稿，不替代投标负责人审核。")

if not current_id:
    st.title("投标初稿助手")
    st.info("请先在左侧创建一个项目。")
    st.stop()

project = store.get_project(current_id)
st.title(project["name"])
st.markdown(
    f'<div class="status-note">项目状态：{project["status"]}。流程中的分析结果和正文均可人工修改。</div>',
    unsafe_allow_html=True,
)

tab_upload, tab_analysis, tab_knowledge, tab_generate, tab_export = st.tabs(
    ["1. 招标文件", "2. 分析确认", "3. 知识资料", "4. 章节生成", "5. Word 导出"]
)

with tab_upload:
    st.subheader("上传并解析招标文件")
    uploaded = st.file_uploader(
        "支持 PDF、DOCX、TXT、Markdown",
        type=[extension.lstrip(".") for extension in sorted(SUPPORTED_EXTENSIONS)],
        key=f"tender_{current_id}",
    )
    if st.button("保存并解析", type="primary", disabled=uploaded is None, key=f"parse_{current_id}"):
        try:
            path = store.save_source(current_id, uploaded.name, uploaded.getvalue())
            parsed = parse_document(path)
            store.save_json(current_id, "parsed", parsed.model_dump())
            store.update_project(current_id, status="parsed")
            st.success("解析完成")
            st.rerun()
        except DocumentParseError as exc:
            st.error(str(exc))

    parsed_payload = store.load_json(current_id, "parsed")
    if parsed_payload:
        parsed = ParsedDocument.model_validate(parsed_payload)
        columns = st.columns(3)
        columns[0].metric("字符数", parsed.char_count)
        columns[1].metric("页数", len(parsed.pages))
        columns[2].metric("扫描件风险", "是" if parsed.possible_scanned_document else "否")
        for warning in parsed.warnings:
            st.warning(warning)
        with st.expander("查看解析文本"):
            st.text_area("解析结果", parsed.full_text, height=420, disabled=True, label_visibility="collapsed")

with tab_analysis:
    parsed_payload = store.load_json(current_id, "parsed")
    if not parsed_payload:
        st.info("请先上传并解析招标文件。")
    else:
        parsed = ParsedDocument.model_validate(parsed_payload)
        mode = st.radio(
            "分析方式",
            ["规则模式", "LLM 增强"],
            horizontal=True,
            key=f"analysis_mode_{current_id}",
            help="规则模式无需模型；LLM 增强失败时会自动回退。",
        )
        if st.button("开始分析", type="primary", key=f"analyze_{current_id}"):
            with st.spinner("正在提取招标要求..."):
                analysis = analyze_document(parsed, llm_client, use_llm=mode == "LLM 增强")
                store.save_json(current_id, "analysis", analysis.model_dump())
                store.update_project(current_id, status="analysis_pending_confirmation")
            st.success("分析完成，请逐项确认")
            st.rerun()

        analysis = _load_analysis(store, current_id)
        if analysis:
            for warning in analysis.warnings:
                st.warning(warning)
            st.caption(f"分析模式：{analysis.analysis_mode}")
            st.subheader("项目基本信息")
            info_columns = st.columns(2)
            project_name = info_columns[0].text_input("项目名称", analysis.project_info.project_name, key=f"info_name_{current_id}")
            purchaser = info_columns[1].text_input("招标人/采购人", analysis.project_info.purchaser, key=f"info_purchaser_{current_id}")
            agency = info_columns[0].text_input("代理机构", analysis.project_info.agency, key=f"info_agency_{current_id}")
            budget = info_columns[1].text_input("预算/最高限价", analysis.project_info.budget, key=f"info_budget_{current_id}")
            deadline = st.text_input("投标截止时间", analysis.project_info.bid_deadline, key=f"info_deadline_{current_id}")

            mandatory = _requirement_editor("强制要求", analysis.mandatory_requirements, f"mandatory_{current_id}")
            scoring = _scoring_editor(analysis.scoring_items, f"scoring_{current_id}")
            with st.expander("资格、材料、时间和风险", expanded=False):
                qualifications = _requirement_editor("资格要求", analysis.qualification_requirements, f"qualification_{current_id}")
                documents = _requirement_editor("所需材料", analysis.required_documents, f"documents_{current_id}")
                deadlines = _requirement_editor("时间节点", analysis.deadlines, f"deadlines_{current_id}")
                risks = _requirement_editor("废标风险", analysis.risks, f"risks_{current_id}")

            if st.button("保存人工确认结果", type="primary", key=f"save_analysis_{current_id}"):
                analysis.project_info = ProjectInfo(
                    project_name=project_name,
                    purchaser=purchaser,
                    agency=agency,
                    budget=budget,
                    bid_deadline=deadline,
                )
                analysis.mandatory_requirements = mandatory
                analysis.scoring_items = scoring
                analysis.qualification_requirements = qualifications
                analysis.required_documents = documents
                analysis.deadlines = deadlines
                analysis.risks = risks
                store.save_json(current_id, "analysis", analysis.model_dump())
                store.update_project(current_id, name=project_name or project["name"], status="analysis_confirmed")
                st.success("确认结果已保存")
                st.rerun()

with tab_knowledge:
    st.subheader("项目知识资料")
    st.caption("第一版只保留企业资料、产品资料、历史方案三类，避免知识库结构过度复杂。")
    category = st.selectbox(
        "资料类别",
        list(CATEGORY_LABELS),
        format_func=lambda value: CATEGORY_LABELS[value],
        key=f"knowledge_category_{current_id}",
    )
    knowledge_uploads = st.file_uploader(
        "上传资料",
        type=[extension.lstrip(".") for extension in sorted(SUPPORTED_EXTENSIONS)],
        accept_multiple_files=True,
        key=f"knowledge_upload_{current_id}",
    )
    if st.button("保存资料", disabled=not knowledge_uploads, key=f"save_knowledge_{current_id}"):
        for item in knowledge_uploads:
            store.save_knowledge_file(current_id, category, item.name, item.getvalue())
        st.success(f"已保存 {len(knowledge_uploads)} 个文件")
        st.rerun()

    files_by_category = store.list_knowledge_files(current_id)
    for category_id, paths in files_by_category.items():
        with st.expander(f"{CATEGORY_LABELS[category_id]}（{len(paths)}）", expanded=bool(paths)):
            if paths:
                for path in paths:
                    st.write(path.name)
            else:
                st.caption("暂无资料")

with tab_generate:
    analysis = _load_analysis(store, current_id)
    if not analysis:
        st.info("请先完成招标分析。")
    else:
        st.subheader("章节计划")
        outline_rows = [item.model_dump() for item in analysis.outline]
        outline_editor = st.data_editor(
            outline_rows,
            num_rows="dynamic",
            use_container_width=True,
            hide_index=True,
            key=f"outline_{current_id}",
            column_config={
                "id": None,
                "title": st.column_config.TextColumn("章节名称", required=True, width="medium"),
                "selected": st.column_config.CheckboxColumn("生成", default=True),
                "instructions": st.column_config.TextColumn("编写要求", width="large"),
                "requirement_ids": None,
            },
        )
        outline_records = outline_editor.to_dict("records") if isinstance(outline_editor, pd.DataFrame) else outline_editor
        edited_outline: list[ChapterPlan] = []
        original_by_id = {item.id: item for item in analysis.outline}
        for row in outline_records:
            title = str(_clean_value(row.get("title"))).strip()
            if not title:
                continue
            item_id = str(_clean_value(row.get("id")))
            original = original_by_id.get(item_id)
            edited_outline.append(
                ChapterPlan(
                    id=item_id or ChapterPlan(title=title).id,
                    title=title,
                    selected=bool(_clean_value(row.get("selected"), True)),
                    instructions=str(_clean_value(row.get("instructions"))),
                    requirement_ids=original.requirement_ids if original else [],
                )
            )
        if st.button("保存章节计划", key=f"save_outline_{current_id}"):
            analysis.outline = edited_outline
            store.save_json(current_id, "analysis", analysis.model_dump())
            st.success("章节计划已保存")

        generation_mode = st.radio(
            "生成方式",
            ["规则草稿", "LLM 生成"],
            horizontal=True,
            key=f"generation_mode_{current_id}",
        )
        selected_chapters = [item for item in edited_outline if item.selected]
        if st.button(
            f"生成所选章节（{len(selected_chapters)}）",
            type="primary",
            disabled=not selected_chapters,
            key=f"generate_{current_id}",
        ):
            files_by_category = store.list_knowledge_files(current_id)
            drafts: list[ChapterDraft] = []
            progress = st.progress(0, text="准备生成")
            for index, plan in enumerate(selected_chapters, start=1):
                query = f"{plan.title}\n{plan.instructions}"
                evidence = search_knowledge(query, files_by_category)
                draft = generate_chapter(
                    plan,
                    analysis,
                    evidence,
                    llm_client,
                    use_llm=generation_mode == "LLM 生成",
                )
                drafts.append(draft)
                progress.progress(index / len(selected_chapters), text=f"已完成：{plan.title}")
            store.save_json(current_id, "drafts", [draft.model_dump() for draft in drafts])
            store.update_project(current_id, status="draft_generated")
            st.success("章节草稿已生成")
            st.rerun()

        draft_payload = store.load_json(current_id, "drafts", [])
        drafts = [ChapterDraft.model_validate(item) for item in draft_payload]
        if drafts:
            st.subheader("草稿编辑")
            edited_drafts: list[ChapterDraft] = []
            for draft in drafts:
                with st.expander(draft.title, expanded=True):
                    markdown = st.text_area(
                        "Markdown 正文",
                        draft.markdown,
                        height=360,
                        key=f"draft_{current_id}_{draft.chapter_id}",
                        label_visibility="collapsed",
                    )
                    if draft.evidence_sources:
                        st.caption("引用资料：" + "、".join(draft.evidence_sources))
                    edited_drafts.append(
                        ChapterDraft(
                            chapter_id=draft.chapter_id,
                            title=draft.title,
                            markdown=markdown,
                            evidence_sources=draft.evidence_sources,
                        )
                    )
            if st.button("保存正文修改", key=f"save_drafts_{current_id}"):
                store.save_json(current_id, "drafts", [draft.model_dump() for draft in edited_drafts])
                st.success("正文修改已保存")

with tab_export:
    analysis = _load_analysis(store, current_id)
    draft_payload = store.load_json(current_id, "drafts", [])
    drafts = [ChapterDraft.model_validate(item) for item in draft_payload]
    if not analysis or not drafts:
        st.info("完成分析和章节生成后才能导出 Word。")
    else:
        st.subheader("导出固定格式 Word 初稿")
        st.write(f"准备导出 {len(drafts)} 个章节，并附带强制要求、评分项和待核对事项。")
        project_name = analysis.project_info.project_name or project["name"]
        output_name = safe_filename(f"{project_name}_投标文件初稿.docx")
        output_path = store.output_path(current_id, output_name)
        if st.button("生成 Word", type="primary", key=f"export_{current_id}"):
            with st.spinner("正在生成 Word..."):
                export_docx(output_path, analysis, drafts)
                store.update_project(current_id, status="exported")
            st.success("Word 已生成")
        if output_path.exists():
            st.download_button(
                "下载 Word",
                data=output_path.read_bytes(),
                file_name=output_path.name,
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
            )
            st.caption(str(output_path))
