from __future__ import annotations

import mimetypes
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
    ReviewIssue,
    ReviewReport,
    ScoringItem,
    SubmissionItem,
    TenderAnalysis,
)
from bid_assistant.packager import build_package_readiness, create_submission_package
from bid_assistant.parsers import DocumentParseError, SUPPORTED_EXTENSIONS, parse_document
from bid_assistant.reviewer import build_export_checklist, build_review_report
from bid_assistant.storage import ProjectArchiveError, ProjectStore, safe_filename
from bid_assistant.submission import (
    ATTACHMENT_CATEGORY_LABELS,
    SUBMISSION_CATEGORIES,
    summarize_submission_items,
    sync_submission_items,
)


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
REVIEW_STATUS_OPTIONS = ["待处理", "已处理", "忽略"]
SUBMISSION_STATUS_OPTIONS = ["待准备", "已备妥", "不适用"]
PROJECT_STATUS_LABELS = {
    "new": "新建",
    "uploaded": "已上传",
    "parsed": "已解析",
    "analysis_pending_confirmation": "分析待确认",
    "analysis_confirmed": "分析已确认",
    "knowledge_ready": "资料已就绪",
    "draft_generated": "草稿已生成",
    "review_generated": "复核报告已生成",
    "exported": "Word 已导出",
    "packaged": "提交包已生成",
}


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
        width="stretch",
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
        width="stretch",
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


def _load_review(store: ProjectStore, project_id: str) -> ReviewReport | None:
    payload = store.load_json(project_id, "review")
    return ReviewReport.model_validate(payload) if payload else None


def _load_submission_items(store: ProjectStore, project_id: str) -> list[SubmissionItem]:
    payload = store.load_json(project_id, "submission_checklist", [])
    return [SubmissionItem.model_validate(item) for item in payload]


def _submission_rows(items: list[SubmissionItem]) -> list[dict]:
    rows = []
    for item in items:
        row = item.model_dump()
        row["attachment"] = item.attachment or "未关联"
        rows.append(row)
    return rows


store = get_store()
llm_client = get_llm_client()

st.sidebar.title("投标项目")
new_name = st.sidebar.text_input("新项目名称", placeholder="例如：某某信息化项目")
if st.sidebar.button("新建项目", type="primary", width="stretch"):
    project = store.create_project(new_name)
    st.session_state["project_id"] = project["id"]
    st.rerun()

show_archived = st.sidebar.toggle("显示已归档项目", key="show_archived_projects")
projects = store.list_projects(include_archived=show_archived)
project_ids = [project["id"] for project in projects]
project_names = {
    project["id"]: f"{project['name']}（已归档）" if project["archived"] else project["name"]
    for project in projects
}
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

with st.sidebar.expander("项目管理", expanded=False):
    backup_upload = st.file_uploader(
        "导入项目备份",
        type=["zip"],
        key="project_backup_upload",
        help="仅支持由本系统导出的完整项目 ZIP 备份。",
    )
    if st.button("导入备份", disabled=backup_upload is None, width="stretch"):
        try:
            imported = store.import_project_archive(backup_upload.getvalue())
            st.session_state["project_id"] = imported["id"]
            st.success(f"已导入：{imported['name']}")
            st.rerun()
        except ProjectArchiveError as exc:
            st.error(str(exc))

    if current_id:
        managed_project = store.get_project(current_id)
        try:
            backup_data = store.export_project_archive(current_id)
            backup_name = safe_filename(f"{managed_project['name']}_完整备份.zip")
            st.download_button(
                "下载完整备份",
                data=backup_data,
                file_name=backup_name,
                mime="application/zip",
                width="stretch",
            )
        except ProjectArchiveError as exc:
            st.warning(str(exc))

        if st.button(
            "复制项目",
            width="stretch",
            key=f"duplicate_{current_id}",
            help="复制源文件、分析、知识资料、草稿和复核状态，不复制旧 Word 与版本历史。",
        ):
            duplicate = store.duplicate_project(current_id)
            st.session_state["project_id"] = duplicate["id"]
            st.session_state["project_flash"] = f"已创建项目副本：{duplicate['name']}"
            st.rerun()

        archive_label = "恢复项目" if managed_project["archived"] else "归档项目"
        if st.button(archive_label, width="stretch", key=f"archive_{current_id}"):
            store.set_project_archived(current_id, not bool(managed_project["archived"]))
            if not managed_project["archived"] and not show_archived:
                st.session_state.pop("project_id", None)
            st.rerun()

with st.sidebar.expander("模型配置", expanded=False):
    st.code(f"{settings.llm_model}\n{settings.llm_base_url}")
    if st.button("检测模型连接", width="stretch"):
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
flash_message = st.session_state.pop("project_flash", None)
if flash_message:
    st.success(flash_message)
status_label = PROJECT_STATUS_LABELS.get(project["status"], project["status"])
st.markdown(
    f'<div class="status-note">项目状态：{status_label}。流程中的分析结果和正文均可人工修改。</div>',
    unsafe_allow_html=True,
)
if project["archived"]:
    st.warning("当前项目已归档，可在左侧“项目管理”中恢复。")

workflow = store.project_progress(current_id)
st.progress(
    workflow["completed"] / workflow["total"],
    text=f"项目流程：已完成 {workflow['completed']} / {workflow['total']}（{workflow['percent']}%）",
)
step_columns = st.columns(workflow["total"])
for column, step in zip(step_columns, workflow["steps"], strict=True):
    column.caption(f"{'✓' if step['complete'] else '○'} {step['label']}")
st.caption(
    f"已关联知识资料：{workflow['knowledge_files']} 个文件 · "
    f"提交附件：{workflow['attachment_files']} 个文件"
)

tab_upload, tab_analysis, tab_knowledge, tab_generate, tab_review, tab_export, tab_submission = st.tabs(
    [
        "1. 招标文件",
        "2. 分析确认",
        "3. 知识资料",
        "4. 章节生成",
        "5. 复核检查",
        "6. Word 导出",
        "7. 提交清单",
    ]
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
                store.delete_json(current_id, "review")
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
                store.delete_json(current_id, "review")
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
            width="stretch",
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
            store.delete_json(current_id, "review")
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
            generated_payload = [draft.model_dump() for draft in drafts]
            store.save_json(current_id, "drafts", generated_payload)
            store.save_draft_version(current_id, generated_payload, f"{generation_mode}生成")
            store.delete_json(current_id, "review")
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
                edited_payload = [draft.model_dump() for draft in edited_drafts]
                store.save_json(current_id, "drafts", edited_payload)
                store.save_draft_version(current_id, edited_payload, "人工保存")
                store.delete_json(current_id, "review")
                store.update_project(current_id, status="draft_generated")
                st.success("正文修改已保存")

            versions = store.list_draft_versions(current_id)
            with st.expander(f"草稿版本记录（{len(versions)}）", expanded=False):
                if not versions:
                    st.caption("首次生成或保存正文后会自动建立版本快照。")
                else:
                    versions_by_id = {item["id"]: item for item in versions}

                    def version_label(version_id: str) -> str:
                        item = versions_by_id[version_id]
                        created_at = item["created_at"].replace("T", " ").replace("+00:00", " UTC")
                        return f"{created_at} | {item['reason']} | {item['draft_count']} 章"

                    selected_version_id = st.selectbox(
                        "选择草稿版本",
                        list(versions_by_id),
                        format_func=version_label,
                        key=f"draft_version_{current_id}",
                    )
                    selected_version = store.load_draft_version(current_id, selected_version_id)
                    chapter_names = [
                        str(item.get("title", "未命名章节")) for item in selected_version["drafts"]
                    ]
                    st.caption("包含章节：" + "、".join(chapter_names))
                    if st.button(
                        "恢复此版本",
                        key=f"restore_draft_version_{current_id}",
                        help="恢复前会自动保存当前正文，已有复核报告将失效。",
                    ):
                        store.restore_draft_version(current_id, selected_version_id)
                        key_prefix = f"draft_{current_id}_"
                        for state_key in list(st.session_state):
                            if str(state_key).startswith(key_prefix):
                                del st.session_state[state_key]
                        st.session_state["project_flash"] = "草稿版本已恢复，请重新生成复核报告。"
                        st.rerun()
                st.caption("每个项目最多保留最近 50 个草稿版本，完整备份会包含这些版本。")

with tab_review:
    analysis = _load_analysis(store, current_id)
    draft_payload = store.load_json(current_id, "drafts", [])
    drafts = [ChapterDraft.model_validate(item) for item in draft_payload]
    if not analysis:
        st.info("完成招标分析后才能执行复核检查。")
    else:
        if st.button("生成或刷新复核报告", type="primary", key=f"review_{current_id}"):
            review = build_review_report(analysis, drafts)
            store.save_json(current_id, "review", review.model_dump())
            store.update_project(current_id, status="review_generated")
            st.rerun()

        review = _load_review(store, current_id)
        if review is None:
            st.info("尚未生成复核报告。")
        else:
            metrics = st.columns(4)
            metrics[0].metric("待处理", review.pending_count())
            metrics[1].metric("高风险", review.severity_count("高"))
            metrics[2].metric("中风险", review.severity_count("中"))
            metrics[3].metric("低风险", review.severity_count("低"))

            issue_rows = [
                {
                    "id": item.id,
                    "severity": item.severity,
                    "category": item.category,
                    "status": item.status,
                    "source_page": item.source_page,
                    "detail": f"{item.message}\n处理建议：{item.suggestion}",
                }
                for item in review.issues
            ]
            edited_issues = st.data_editor(
                issue_rows,
                width="stretch",
                hide_index=True,
                height=520,
                key=f"review_editor_{current_id}",
                disabled=["id", "severity", "category", "detail", "source_page"],
                column_config={
                    "id": None,
                    "severity": st.column_config.TextColumn("级别", width="small"),
                    "category": st.column_config.TextColumn("类别", width="small"),
                    "status": st.column_config.SelectboxColumn(
                        "处理状态", options=REVIEW_STATUS_OPTIONS, required=True, width="small"
                    ),
                    "source_page": st.column_config.NumberColumn("页码", width="small"),
                    "detail": st.column_config.TextColumn("问题与处理建议", width="large"),
                },
            )
            records = edited_issues.to_dict("records") if isinstance(edited_issues, pd.DataFrame) else edited_issues
            if st.button("保存复核状态", key=f"save_review_{current_id}"):
                original_by_id = {item.id: item for item in review.issues}
                updated: list[ReviewIssue] = []
                for row in records:
                    item_id = str(_clean_value(row.get("id")))
                    original = original_by_id.get(item_id)
                    if original is None:
                        continue
                    original.status = str(_clean_value(row.get("status"), "待处理"))
                    updated.append(original)
                review.issues = updated
                store.save_json(current_id, "review", review.model_dump())
                st.success("复核状态已保存")
                st.rerun()

with tab_export:
    analysis = _load_analysis(store, current_id)
    draft_payload = store.load_json(current_id, "drafts", [])
    drafts = [ChapterDraft.model_validate(item) for item in draft_payload]
    if not analysis or not drafts:
        st.info("完成分析和章节生成后才能导出 Word。")
    else:
        st.subheader("导出 Word 初稿")
        review = _load_review(store, current_id) or build_review_report(analysis, drafts)
        readiness = build_export_checklist(analysis, drafts, review)
        versions = store.list_export_versions(current_id)

        metrics = st.columns(4)
        metrics[0].metric("章节", len(drafts))
        metrics[1].metric("待处理", readiness["pending_count"])
        metrics[2].metric("高风险", readiness["high_count"])
        metrics[3].metric("Word 版本", len(versions))

        status_labels = {"pass": "通过", "warning": "需确认", "block": "不可导出"}
        checklist_rows = [
            {
                "check": item["label"],
                "status": status_labels[item["status"]],
                "detail": item["detail"],
            }
            for item in readiness["checks"]
        ]
        st.dataframe(
            checklist_rows,
            width="stretch",
            hide_index=True,
            column_config={
                "check": st.column_config.TextColumn("检查项", width="small"),
                "status": st.column_config.TextColumn("结果", width="small"),
                "detail": st.column_config.TextColumn("说明", width="large"),
            },
        )

        acknowledged = not readiness["requires_confirmation"]
        if readiness["blocking_count"]:
            st.error("存在不可导出项，请先完成章节草稿。")
        elif readiness["requires_confirmation"]:
            acknowledged = st.checkbox(
                "我已知悉未处理风险，本次文件仅用于内部复核",
                key=f"export_ack_{current_id}",
            )

        project_name = analysis.project_info.project_name or project["name"]
        output_name = safe_filename(f"{project_name}_投标文件初稿.docx")
        version_note = st.text_input(
            "版本说明",
            placeholder="例如：第一次内部评审",
            key=f"export_note_{current_id}",
        )
        if st.button(
            "生成新版本",
            type="primary",
            key=f"export_{current_id}",
            disabled=not readiness["can_export"] or not acknowledged,
            icon=":material/description:",
        ):
            with st.spinner("正在生成 Word..."):
                store.save_json(current_id, "review", review.model_dump())
                target = store.next_output_version(current_id, output_name)
                export_docx(target["path"], analysis, drafts, review)
                store.record_export_version(
                    current_id,
                    target["path"],
                    version=target["version"],
                    chapter_count=len(drafts),
                    review_summary={
                        "pending": review.pending_count(),
                        "high": review.severity_count("高"),
                        "medium": review.severity_count("中"),
                        "low": review.severity_count("低"),
                    },
                    warning_count=readiness["warning_count"],
                    note=version_note,
                )
                store.update_project(current_id, status="exported")
            st.session_state["project_flash"] = f"Word V{target['version']:03d} 已生成"
            st.rerun()

        versions = store.list_export_versions(current_id)
        if versions:
            st.subheader("Word 版本记录")
            history_rows = []
            for item in versions:
                summary = item["review_summary"]
                history_rows.append(
                    {
                        "version": f"V{item['version']:03d}",
                        "created_at": item["created_at"].replace("T", " ").replace("+00:00", " UTC"),
                        "chapters": item["chapter_count"],
                        "pending": summary.get("pending", 0),
                        "high": summary.get("high", 0),
                        "note": item["note"] or "-",
                    }
                )
            st.dataframe(
                history_rows,
                width="stretch",
                hide_index=True,
                column_config={
                    "version": st.column_config.TextColumn("版本", width="small"),
                    "created_at": st.column_config.TextColumn("生成时间", width="medium"),
                    "chapters": st.column_config.NumberColumn("章节", width="small"),
                    "pending": st.column_config.NumberColumn("待处理", width="small"),
                    "high": st.column_config.NumberColumn("高风险", width="small"),
                    "note": st.column_config.TextColumn("版本说明", width="large"),
                },
            )

            version_by_id = {item["id"]: item for item in versions}
            selected_version_id = st.selectbox(
                "选择下载版本",
                options=list(version_by_id),
                format_func=lambda version_id: (
                    f"V{version_by_id[version_id]['version']:03d} | "
                    f"{version_by_id[version_id]['filename']}"
                ),
                key=f"export_version_{current_id}",
            )
            selected_version = version_by_id[selected_version_id]
            st.download_button(
                "下载所选 Word",
                data=selected_version["path"].read_bytes(),
                file_name=selected_version["filename"],
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                width="stretch",
                icon=":material/download:",
            )
            st.caption(str(selected_version["path"]))
        else:
            st.caption("尚未生成 Word 版本。")


with tab_submission:
    st.subheader("附件目录与最终提交清单")
    analysis = _load_analysis(store, current_id)
    submission_items = _load_submission_items(store, current_id)
    attachment_files = store.list_attachment_files(current_id)
    attachment_refs = {
        f"{ATTACHMENT_CATEGORY_LABELS[category_id]}/{path.name}"
        for category_id, paths in attachment_files.items()
        for path in paths
    }
    submission_summary = summarize_submission_items(submission_items, attachment_refs)

    summary_columns = st.columns(5)
    summary_columns[0].metric("清单项", submission_summary["total"])
    summary_columns[1].metric("必交项", submission_summary["required"])
    summary_columns[2].metric("已备妥", submission_summary["ready"])
    summary_columns[3].metric("待准备", submission_summary["pending_required"])
    summary_columns[4].metric("已关联附件", submission_summary["linked"])

    if submission_summary["complete"]:
        st.success("所有必交项均已备妥或明确标记为不适用。")
    elif submission_items:
        st.warning(f"仍有 {submission_summary['pending_required']} 个必交项待准备。")
    if submission_summary["broken_links"]:
        st.error(f"有 {submission_summary['broken_links']} 个清单项关联的附件已不存在。")

    sync_label = "同步招标材料" if submission_items else "从分析结果生成清单"
    if st.button(
        sync_label,
        disabled=analysis is None,
        key=f"sync_submission_{current_id}",
        icon=":material/sync:",
    ):
        synced = sync_submission_items(analysis, submission_items)
        store.save_json(current_id, "submission_checklist", [item.model_dump() for item in synced])
        st.session_state.pop(f"submission_editor_{current_id}", None)
        st.session_state["project_flash"] = f"提交清单已同步，共 {len(synced)} 项"
        st.rerun()

    st.markdown("#### 提交附件")
    upload_columns = st.columns([1, 3])
    attachment_category = upload_columns[0].selectbox(
        "附件类别",
        options=list(ATTACHMENT_CATEGORY_LABELS),
        format_func=lambda value: ATTACHMENT_CATEGORY_LABELS[value],
        key=f"attachment_category_{current_id}",
    )
    attachment_uploads = upload_columns[1].file_uploader(
        "选择附件",
        accept_multiple_files=True,
        key=(
            f"attachment_upload_{current_id}_"
            f"{st.session_state.get(f'attachment_upload_nonce_{current_id}', 0)}"
        ),
    )
    if st.button(
        "保存附件",
        disabled=not attachment_uploads,
        key=f"save_attachments_{current_id}",
        icon=":material/upload_file:",
    ):
        for item in attachment_uploads:
            store.save_attachment_file(current_id, attachment_category, item.name, item.getvalue())
        nonce_key = f"attachment_upload_nonce_{current_id}"
        st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
        st.session_state["project_flash"] = f"已保存 {len(attachment_uploads)} 个提交附件"
        st.rerun()

    attachment_files = store.list_attachment_files(current_id)
    for category_id, label in ATTACHMENT_CATEGORY_LABELS.items():
        paths = attachment_files[category_id]
        with st.expander(f"{label}（{len(paths)}）", expanded=bool(paths)):
            if not paths:
                st.caption("暂无附件")
                continue
            for path in paths:
                reference = f"{category_id}/{path.name}"
                file_columns = st.columns([5, 1, 1])
                file_columns[0].write(f"{path.name} · {path.stat().st_size / 1024:.1f} KB")
                file_columns[1].download_button(
                    "下载",
                    data=path.read_bytes(),
                    file_name=path.name,
                    mime=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    key=f"download_attachment_{current_id}_{reference}",
                    icon=":material/download:",
                )
                if file_columns[2].button(
                    "移除",
                    key=f"delete_attachment_{current_id}_{reference}",
                    icon=":material/delete:",
                ):
                    store.delete_attachment_file(current_id, reference)
                    st.session_state["project_flash"] = f"已移除附件：{path.name}"
                    st.rerun()

    st.markdown("#### 最终提交材料清单")
    attachment_files = store.list_attachment_files(current_id)
    current_attachment_refs = {
        f"{ATTACHMENT_CATEGORY_LABELS[category_id]}/{path.name}"
        for category_id, paths in attachment_files.items()
        for path in paths
    }
    existing_attachment_refs = {item.attachment for item in submission_items if item.attachment}
    attachment_options = ["未关联"] + sorted(current_attachment_refs | existing_attachment_refs)
    submission_columns = [
        "id",
        "category",
        "name",
        "source_requirement_id",
        "source_page",
        "required",
        "status",
        "attachment",
        "note",
    ]
    submission_frame = pd.DataFrame(_submission_rows(submission_items), columns=submission_columns)
    edited_submission = st.data_editor(
        submission_frame,
        key=f"submission_editor_{current_id}",
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "id": None,
            "source_requirement_id": None,
            "category": st.column_config.SelectboxColumn(
                "类别", options=SUBMISSION_CATEGORIES, required=True, width="small"
            ),
            "name": st.column_config.TextColumn("材料名称", required=True, width="large"),
            "source_page": st.column_config.NumberColumn("页码", min_value=1, step=1, width="small"),
            "required": st.column_config.CheckboxColumn("必交", default=True, width="small"),
            "status": st.column_config.SelectboxColumn(
                "状态", options=SUBMISSION_STATUS_OPTIONS, required=True, width="small"
            ),
            "attachment": st.column_config.SelectboxColumn(
                "关联附件", options=attachment_options, width="medium"
            ),
            "note": st.column_config.TextColumn("备注", width="large"),
        },
    )
    submission_records = (
        edited_submission.to_dict("records") if isinstance(edited_submission, pd.DataFrame) else edited_submission
    )
    edited_items: list[SubmissionItem] = []
    for row in submission_records:
        name = str(_clean_value(row.get("name"))).strip()
        if not name:
            continue
        category = str(_clean_value(row.get("category"), "其他"))
        status = str(_clean_value(row.get("status"), "待准备"))
        edited_items.append(
            SubmissionItem(
                id=str(_clean_value(row.get("id"), "")) or SubmissionItem(name=name).id,
                category=category if category in SUBMISSION_CATEGORIES else "其他",
                name=name,
                source_requirement_id=str(_clean_value(row.get("source_requirement_id"))),
                source_page=int(row["source_page"]) if _clean_value(row.get("source_page"), None) else None,
                required=bool(_clean_value(row.get("required"), True)),
                status=status if status in SUBMISSION_STATUS_OPTIONS else "待准备",
                attachment=(
                    ""
                    if str(_clean_value(row.get("attachment"))) == "未关联"
                    else str(_clean_value(row.get("attachment")))
                ),
                note=str(_clean_value(row.get("note"))).strip(),
            )
        )

    action_columns = st.columns([1, 1, 4])
    if action_columns[0].button(
        "保存清单",
        type="primary",
        key=f"save_submission_{current_id}",
        icon=":material/save:",
    ):
        store.save_json(current_id, "submission_checklist", [item.model_dump() for item in edited_items])
        saved_summary = summarize_submission_items(edited_items, current_attachment_refs)
        message = "提交清单已完成" if saved_summary["complete"] else "提交清单已保存"
        st.session_state["project_flash"] = message
        st.rerun()

    csv_rows = [
        {
            "类别": item.category,
            "材料名称": item.name,
            "原文页码": item.source_page or "",
            "必交": "是" if item.required else "否",
            "状态": item.status,
            "关联附件": item.attachment,
            "备注": item.note,
        }
        for item in edited_items
    ]
    csv_data = ("\ufeff" + pd.DataFrame(csv_rows).to_csv(index=False)).encode("utf-8")
    action_columns[1].download_button(
        "导出清单",
        data=csv_data,
        file_name=safe_filename(f"{project['name']}_最终提交材料清单.csv"),
        mime="text/csv;charset=utf-8",
        disabled=not edited_items,
        key=f"download_submission_{current_id}",
        icon=":material/download:",
    )

    st.markdown("#### 提交包")
    export_versions = store.list_export_versions(current_id)
    selected_word_version = None
    if export_versions:
        word_version_by_id = {item["id"]: item for item in export_versions}
        selected_word_id = st.selectbox(
            "选择用于打包的 Word 版本",
            options=list(word_version_by_id),
            format_func=lambda version_id: (
                f"V{word_version_by_id[version_id]['version']:03d} | "
                f"{word_version_by_id[version_id]['filename']}"
            ),
            key=f"package_word_version_{current_id}",
        )
        selected_word_version = word_version_by_id[selected_word_id]
    else:
        st.info("请先在“6. Word 导出”中生成至少一个 Word 版本。")

    has_unsaved_changes = (
        [item.model_dump() for item in edited_items]
        != [item.model_dump() for item in submission_items]
    )
    review = _load_review(store, current_id)
    package_readiness = build_package_readiness(
        selected_word_version,
        submission_items,
        current_attachment_refs,
        review,
        has_unsaved_changes=has_unsaved_changes,
    )
    package_status_labels = {"pass": "通过", "warning": "需确认", "block": "不可打包"}
    st.dataframe(
        [
            {
                "check": item["label"],
                "status": package_status_labels[item["status"]],
                "detail": item["detail"],
            }
            for item in package_readiness["checks"]
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "check": st.column_config.TextColumn("检查项", width="small"),
            "status": st.column_config.TextColumn("结果", width="small"),
            "detail": st.column_config.TextColumn("说明", width="large"),
        },
    )

    package_acknowledged = not package_readiness["requires_confirmation"]
    if package_readiness["blocking_count"]:
        st.error(f"存在 {package_readiness['blocking_count']} 个不可打包项，请先处理后再生成提交包。")
    if package_readiness["requires_confirmation"]:
        package_acknowledged = st.checkbox(
            "我已知悉复核风险，本次生成包仅用于内部预审",
            key=f"package_ack_{current_id}",
        )

    package_note = st.text_input(
        "提交包版本说明",
        placeholder="例如：第一次内部预审",
        key=f"package_note_{current_id}",
    )
    if st.button(
        "生成提交包",
        type="primary",
        key=f"create_package_{current_id}",
        disabled=not package_readiness["can_package"] or not package_acknowledged,
        icon=":material/folder_zip:",
    ):
        with st.spinner("正在核对文件并生成提交包..."):
            target = store.next_package_version(
                current_id,
                safe_filename(f"{project['name']}_最终提交包.zip"),
            )
            create_submission_package(
                target["path"],
                project=project,
                word_version=selected_word_version,
                items=submission_items,
                attachment_files=attachment_files,
                review_summary={
                    "pending": review.pending_count() if review else 0,
                    "high": review.severity_count("高") if review else 0,
                    "medium": review.severity_count("中") if review else 0,
                    "low": review.severity_count("低") if review else 0,
                },
                internal_review_only=package_readiness["requires_confirmation"],
                note=package_note,
            )
            store.record_package_version(
                current_id,
                target["path"],
                version=target["version"],
                word_version=selected_word_version,
                checklist_summary=package_readiness["submission_summary"],
                attachment_count=sum(len(paths) for paths in attachment_files.values()),
                warning_count=package_readiness["warning_count"],
                internal_review_only=package_readiness["requires_confirmation"],
                note=package_note,
            )
            store.update_project(current_id, status="packaged")
        st.session_state["project_flash"] = f"提交包 P{target['version']:03d} 已生成"
        st.rerun()

    package_versions = store.list_package_versions(current_id)
    if package_versions:
        st.markdown("##### 提交包版本记录")
        st.dataframe(
            [
                {
                    "version": f"P{item['version']:03d}",
                    "created_at": item["created_at"].replace("T", " ").replace("+00:00", " UTC"),
                    "word": f"V{item['word_version']:03d}",
                    "attachments": item["attachment_count"],
                    "warnings": item["warning_count"],
                    "scope": "仅内部预审" if item["internal_review_only"] else "可进入提交复核",
                    "note": item["note"] or "-",
                }
                for item in package_versions
            ],
            width="stretch",
            hide_index=True,
            column_config={
                "version": st.column_config.TextColumn("版本", width="small"),
                "created_at": st.column_config.TextColumn("生成时间", width="medium"),
                "word": st.column_config.TextColumn("Word", width="small"),
                "attachments": st.column_config.NumberColumn("附件", width="small"),
                "warnings": st.column_config.NumberColumn("风险确认", width="small"),
                "scope": st.column_config.TextColumn("用途", width="medium"),
                "note": st.column_config.TextColumn("版本说明", width="large"),
            },
        )
        package_by_id = {item["id"]: item for item in package_versions}
        selected_package_id = st.selectbox(
            "选择下载提交包版本",
            options=list(package_by_id),
            format_func=lambda version_id: (
                f"P{package_by_id[version_id]['version']:03d} | "
                f"{package_by_id[version_id]['filename']}"
            ),
            key=f"package_version_{current_id}",
        )
        selected_package = package_by_id[selected_package_id]
        st.download_button(
            "下载所选提交包",
            data=selected_package["path"].read_bytes(),
            file_name=selected_package["filename"],
            mime="application/zip",
            width="stretch",
            icon=":material/download:",
        )
        st.caption(
            f"SHA-256：{selected_package['sha256']} · "
            f"{selected_package['size'] / 1024 / 1024:.2f} MB"
        )
    else:
        st.caption("尚未生成提交包版本。")
