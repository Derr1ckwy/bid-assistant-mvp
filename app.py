from __future__ import annotations

import hashlib
import html
import json
import mimetypes
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from bid_assistant.acceptance import build_acceptance_report, build_analysis_acceptance
from bid_assistant.analyzer import analyze_document
from bid_assistant.auth import UserStore
from bid_assistant.config import Settings, save_llm_settings, settings
from bid_assistant.docx_quality import build_docx_quality_report, verify_docx_output
from bid_assistant.exporter import build_draft_docx, export_docx
from bid_assistant.generator import generate_chapter
from bid_assistant.knowledge import clear_knowledge_cache, search_knowledge
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
    TenderAnalysis,
)
from bid_assistant.ocr import MinerUClient
from bid_assistant.packager import (
    build_package_readiness,
    build_package_verification_report,
    create_submission_package,
    verify_submission_package,
)
from bid_assistant.parsers import (
    KNOWLEDGE_EXTENSIONS,
    TENDER_EXTENSIONS,
    DocumentParseError,
    parse_document,
    parse_document_bytes,
)
from bid_assistant.reviewer import build_export_checklist, build_review_report
from bid_assistant.storage import ProjectArchiveError, ProjectStore, format_beijing_time, safe_filename
from bid_assistant.submission import (
    ATTACHMENT_CATEGORY_LABELS,
    build_attachment_inventory,
)


st.set_page_config(page_title="投标初稿助手", page_icon="📄", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px;}
    h1, h2, h3 {letter-spacing: 0;}
    [data-testid="stMetricValue"] {font-size: 1.4rem;}
    [data-testid="stToolbar"], [data-testid="stStatusWidget"], [data-testid="stToast"],
    [data-testid="stSkillsNudgeAnchor"] {display: none !important;}
    [data-testid="stDataFrameColumnMenu"], [data-testid="stDataFrameStatisticsMenu"] {display: none !important;}
    [data-testid="stFileUploaderDropzone"] button p {font-size: 0;}
    [data-testid="stFileUploaderDropzone"] button p::after {content: "选择文件"; font-size: .875rem;}
    [data-testid="stFileUploaderDropzoneInstructions"] {display: none;}
    .status-note {border-left: 4px solid #2f5597; padding: .65rem .85rem; background: #f4f7fb;}
    .cn-table-wrap {overflow-x: auto; border: 1px solid #d9dee5; border-radius: 4px; margin: .35rem 0 1rem;}
    .cn-table {width: 100%; min-width: 680px; border-collapse: collapse; font-size: .92rem; color: #20262e;}
    .cn-table th {background: #f3f5f7; font-weight: 600; text-align: left; white-space: nowrap;}
    .cn-table th, .cn-table td {padding: .55rem .7rem; border-bottom: 1px solid #e4e7eb; vertical-align: top;}
    .cn-table td {white-space: normal; overflow-wrap: anywhere;}
    .cn-table tr:last-child td {border-bottom: 0;}
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
    "packaged": "交付包已生成",
}


@st.cache_resource
def get_store() -> ProjectStore:
    return ProjectStore(settings.data_dir)


@st.cache_resource
def get_user_store() -> UserStore:
    return UserStore(settings.data_dir / "security" / "auth.db")


@st.cache_resource
def get_llm_client(runtime_settings: Settings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(runtime_settings)


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


def _display_chinese_table(rows: list[dict], columns: list[tuple[str, str]]) -> None:
    """Render read-only data without Streamlit's English grid menus."""
    if not rows:
        return

    def render_value(value) -> str:
        cleaned = _clean_value(value, "-")
        text = str(cleaned) if cleaned != "" else "-"
        return html.escape(text).replace("\n", "<br>")

    header = "".join(f"<th>{html.escape(label)}</th>" for _, label in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{render_value(row.get(key))}</td>" for key, _ in columns) + "</tr>"
        for row in rows
    )
    st.markdown(
        f'<div class="cn-table-wrap"><table class="cn-table"><thead><tr>{header}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _start_authenticated_session(user: dict) -> None:
    st.session_state["auth_user_id"] = user["id"]
    st.session_state["auth_last_activity"] = datetime.now(timezone.utc).isoformat()


def _clear_authenticated_session(notice: str = "") -> None:
    st.session_state.clear()
    if notice:
        st.session_state["auth_notice"] = notice


def _render_first_admin_setup(auth_store: UserStore, store: ProjectStore) -> None:
    st.title("初始化管理员账号")
    st.info("首次启用账号保护。账号密码只保存为本机加盐哈希，不会进入项目备份或 Git。")
    with st.form("first_admin_setup"):
        username = st.text_input("管理员用户名", value="admin", help="使用 3-32 位字母、数字、点、短横线或下划线。")
        display_name = st.text_input("显示名称", value="系统管理员")
        password = st.text_input("管理员密码", type="password")
        confirmation = st.text_input("再次输入密码", type="password")
        submitted = st.form_submit_button("创建管理员并启用登录保护", type="primary")
    if submitted:
        if password != confirmation:
            st.error("两次输入的密码不一致。")
            return
        try:
            user = auth_store.create_user(username, display_name, password, role="admin")
        except ValueError as exc:
            st.error(str(exc))
            return
        claimed = store.assign_unowned_projects(user["id"])
        auth_store.record_event(
            "legacy_projects_claimed",
            actor=user,
            detail={"project_count": claimed},
        )
        _start_authenticated_session(user)
        st.rerun()


def _render_login(auth_store: UserStore) -> None:
    st.title("投标初稿助手")
    st.subheader("账号登录")
    notice = st.session_state.pop("auth_notice", None)
    if notice:
        st.warning(notice)
    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary")
    st.caption("连续 5 次密码错误将锁定账号 10 分钟。关闭或刷新浏览器后可能需要重新登录。")
    if submitted:
        result = auth_store.authenticate(username, password)
        if result["ok"]:
            _start_authenticated_session(result["user"])
            st.rerun()
        st.error(result["message"])


def _authenticated_user(auth_store: UserStore) -> dict | None:
    user_id = st.session_state.get("auth_user_id")
    if not user_id:
        return None
    user = auth_store.get_user(str(user_id))
    if not user or not user["active"]:
        _clear_authenticated_session("账号不存在或已被管理员停用，请重新登录。")
        return None

    now = datetime.now(timezone.utc)
    last_activity = st.session_state.get("auth_last_activity")
    if last_activity:
        try:
            idle_seconds = (now - datetime.fromisoformat(str(last_activity))).total_seconds()
        except ValueError:
            idle_seconds = 0
        if idle_seconds > settings.auth_session_timeout_minutes * 60:
            auth_store.record_event("session_timeout", actor=user)
            _clear_authenticated_session(
                f"会话已闲置超过 {settings.auth_session_timeout_minutes} 分钟，请重新登录。"
            )
            return None
    st.session_state["auth_last_activity"] = now.isoformat()
    return user


def _render_forced_password_change(auth_store: UserStore, user: dict) -> None:
    st.title("修改临时密码")
    st.warning("管理员已重置该账号密码。继续使用前必须设置只有你本人知道的新密码。")
    with st.form("forced_password_change"):
        current_password = st.text_input("当前临时密码", type="password")
        new_password = st.text_input("新密码", type="password")
        confirmation = st.text_input("再次输入新密码", type="password")
        submitted = st.form_submit_button("保存新密码", type="primary")
    if submitted:
        if new_password != confirmation:
            st.error("两次输入的新密码不一致。")
            return
        try:
            auth_store.change_password(user["id"], current_password, new_password)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.success("密码已修改，请继续使用系统。")
        st.rerun()


def _render_account_security(auth_store: UserStore, user: dict) -> None:
    role_label = "管理员" if user["role"] == "admin" else "普通账号"
    with st.sidebar.expander("账号与安全", expanded=False):
        st.write(f"{user['display_name']}（{role_label}）")
        st.caption(f"用户名：{user['username']}")
        with st.form("sidebar_password_change", clear_on_submit=True):
            current_password = st.text_input("当前密码", type="password", key="self_current_password")
            new_password = st.text_input("新密码", type="password", key="self_new_password")
            confirmation = st.text_input("确认新密码", type="password", key="self_confirm_password")
            change_password = st.form_submit_button("修改密码", width="stretch")
        if change_password:
            if new_password != confirmation:
                st.error("两次输入的新密码不一致。")
            else:
                try:
                    auth_store.change_password(user["id"], current_password, new_password)
                    st.success("密码已修改。")
                except ValueError as exc:
                    st.error(str(exc))
        if st.button("退出登录", width="stretch", key="logout"):
            auth_store.record_event("logout", actor=user)
            _clear_authenticated_session("已安全退出登录。")
            st.rerun()


def _render_account_management(auth_store: UserStore, store: ProjectStore, current_user: dict) -> None:
    st.title("账号与项目权限")
    st.caption("账号权限用于隔离系统内项目，不替代 Windows 磁盘权限、BitLocker 或服务器访问控制。")
    users = auth_store.list_users()
    user_by_id = {item["id"]: item for item in users}
    all_projects = store.list_projects(include_archived=True)
    metrics = st.columns(4)
    metrics[0].metric("账号总数", len(users))
    metrics[1].metric("有效账号", sum(bool(item["active"]) for item in users))
    metrics[2].metric("管理员", sum(item["role"] == "admin" and item["active"] for item in users))
    metrics[3].metric("项目总数", len(all_projects))

    st.subheader("新建账号")
    with st.form("create_account_form", clear_on_submit=True):
        columns = st.columns(2)
        username = columns[0].text_input("用户名", help="创建后不能修改。")
        display_name = columns[1].text_input("显示名称")
        role_label = columns[0].selectbox("角色", ["普通账号", "管理员"])
        initial_password = columns[1].text_input("临时密码", type="password")
        confirmation = columns[1].text_input("确认临时密码", type="password")
        create_account = st.form_submit_button("创建账号", type="primary")
    if create_account:
        if initial_password != confirmation:
            st.error("两次输入的临时密码不一致。")
        else:
            try:
                auth_store.create_user(
                    username,
                    display_name,
                    initial_password,
                    role="admin" if role_label == "管理员" else "user",
                    must_change_password=True,
                    actor=current_user,
                )
                st.success("账号已创建，首次登录时必须修改临时密码。")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    st.subheader("账号列表")
    _display_chinese_table(
        [
            {
                "username": item["username"],
                "display_name": item["display_name"],
                "role": "管理员" if item["role"] == "admin" else "普通账号",
                "active": "有效" if item["active"] else "已停用",
                "must_change": "是" if item["must_change_password"] else "否",
                "last_login": format_beijing_time(item["last_login_at"]) if item["last_login_at"] else "从未登录",
            }
            for item in users
        ],
        [
            ("username", "用户名"),
            ("display_name", "显示名称"),
            ("role", "角色"),
            ("active", "状态"),
            ("must_change", "需改密码"),
            ("last_login", "最近登录"),
        ],
    )

    selected_user_id = st.selectbox(
        "选择管理账号",
        options=list(user_by_id),
        format_func=lambda user_id: f"{user_by_id[user_id]['display_name']}（{user_by_id[user_id]['username']}）",
        key="managed_user_id",
    )
    selected_user = user_by_id[selected_user_id]
    action_columns = st.columns([1, 2])
    active_label = "停用账号" if selected_user["active"] else "启用账号"
    if action_columns[0].button(
        active_label,
        disabled=selected_user_id == current_user["id"],
        width="stretch",
        key=f"toggle_user_{selected_user_id}",
    ):
        try:
            auth_store.set_active(selected_user_id, not bool(selected_user["active"]), actor=current_user)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    if selected_user_id != current_user["id"]:
        with st.form(f"reset_password_{selected_user_id}", clear_on_submit=True):
            reset_password = st.text_input("新的临时密码", type="password")
            reset_confirmation = st.text_input("确认新的临时密码", type="password")
            reset_submitted = st.form_submit_button("重置所选账号密码")
        if reset_submitted:
            if reset_password != reset_confirmation:
                st.error("两次输入的临时密码不一致。")
            else:
                try:
                    auth_store.reset_password(selected_user_id, reset_password, actor=current_user)
                    st.success("密码已重置，该账号下次登录时必须修改密码。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.subheader("项目归属")
    if all_projects:
        _display_chinese_table(
            [
                {
                    "name": item["name"],
                    "owner": user_by_id.get(item.get("owner_id"), {}).get("display_name", "未分配"),
                    "status": "已归档" if item["archived"] else "使用中",
                    "updated": format_beijing_time(item["updated_at"]),
                }
                for item in all_projects
            ],
            [("name", "项目"), ("owner", "所属账号"), ("status", "状态"), ("updated", "更新时间")],
        )
        project_by_id = {item["id"]: item for item in all_projects}
        active_users = {item["id"]: item for item in users if item["active"]}
        with st.form("transfer_project_form"):
            transfer_project_id = st.selectbox(
                "选择项目",
                options=list(project_by_id),
                format_func=lambda project_id: project_by_id[project_id]["name"],
            )
            target_owner_id = st.selectbox(
                "转移给",
                options=list(active_users),
                format_func=lambda user_id: f"{active_users[user_id]['display_name']}（{active_users[user_id]['username']}）",
            )
            transfer_submitted = st.form_submit_button("确认转移项目")
        if transfer_submitted:
            store.assign_project_owner(transfer_project_id, target_owner_id)
            auth_store.record_event(
                "project_transferred",
                actor=current_user,
                detail={
                    "project_id": transfer_project_id,
                    "project_name": project_by_id[transfer_project_id]["name"],
                    "target_user_id": target_owner_id,
                    "target_username": active_users[target_owner_id]["username"],
                },
            )
            if st.session_state.get("project_id") == transfer_project_id:
                st.session_state.pop("project_id", None)
            st.success("项目归属已更新。")
            st.rerun()
    else:
        st.info("当前还没有项目。")

    with st.expander("安全审计记录", expanded=False):
        event_labels = {
            "user_created": "创建账号",
            "user_enabled": "启用账号",
            "user_disabled": "停用账号",
            "password_reset": "重置密码",
            "password_changed": "修改密码",
            "login_success": "登录成功",
            "login_failed": "登录失败",
            "login_locked": "锁定期间登录",
            "login_disabled": "停用账号登录",
            "logout": "退出登录",
            "session_timeout": "会话超时",
            "project_transferred": "转移项目",
            "legacy_projects_claimed": "接管历史项目",
        }
        _display_chinese_table(
            [
                {
                    "created_at": format_beijing_time(item["created_at"]),
                    "username": item["username"],
                    "event": event_labels.get(item["event"], item["event"]),
                    "detail": json.dumps(item["detail"], ensure_ascii=False) if item["detail"] else "-",
                }
                for item in auth_store.list_audit_events(100)
            ],
            [("created_at", "时间"), ("username", "账号"), ("event", "事件"), ("detail", "明细")],
        )
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


def _sync_analysis_editor_state(project_id: str, analysis: TenderAnalysis) -> None:
    field_values = {
        f"info_name_{project_id}": analysis.project_info.project_name,
        f"info_purchaser_{project_id}": analysis.project_info.purchaser,
        f"info_agency_{project_id}": analysis.project_info.agency,
        f"info_budget_{project_id}": analysis.project_info.budget,
        f"info_deadline_{project_id}": analysis.project_info.bid_deadline,
    }
    for key, value in field_values.items():
        st.session_state[key] = value

    for prefix in (
        "mandatory",
        "scoring",
        "qualification",
        "documents",
        "deadlines",
        "risks",
    ):
        st.session_state.pop(f"{prefix}_{project_id}", None)


def _load_review(store: ProjectStore, project_id: str) -> ReviewReport | None:
    payload = store.load_json(project_id, "review")
    return ReviewReport.model_validate(payload) if payload else None


def _parsed_fingerprint(document: ParsedDocument) -> str:
    payload = document.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@st.cache_data(show_spinner=False)
def _verify_package_cached(
    path_value: str,
    expected_sha256: str,
    file_size: int,
    modified_ns: int,
) -> dict:
    del file_size, modified_ns
    return verify_submission_package(path_value, expected_sha256=expected_sha256)


@st.cache_data(show_spinner=False)
def _verify_docx_cached(
    path_value: str,
    expected_sha256: str,
    expected_chapter_count: int,
    template_mode: bool,
    file_size: int,
    modified_ns: int,
) -> dict:
    del file_size, modified_ns
    return verify_docx_output(
        path_value,
        expected_sha256=expected_sha256,
        expected_chapter_count=expected_chapter_count,
        template_mode=template_mode,
    )


def _docx_file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError:
        return -1, -1
    return stat.st_size, stat.st_mtime_ns


store = get_store()
auth_store = get_user_store()

if not auth_store.has_users():
    _render_first_admin_setup(auth_store, store)
    st.stop()

current_user = _authenticated_user(auth_store)
if current_user is None:
    _render_login(auth_store)
    st.stop()
if current_user["must_change_password"]:
    _render_forced_password_change(auth_store, current_user)
    st.stop()

is_admin = current_user["role"] == "admin"
st.sidebar.title("投标初稿助手")
_render_account_security(auth_store, current_user)
workspace_options = ["投标项目", "账号管理"] if is_admin else ["投标项目"]
workspace = st.sidebar.radio("工作区", workspace_options, horizontal=True, key="workspace_mode")
if workspace == "账号管理":
    _render_account_management(auth_store, store, current_user)
    st.stop()

runtime_settings = replace(
    settings,
    llm_base_url=str(st.session_state.get("llm_base_url_input", settings.llm_base_url)).rstrip("/"),
    llm_api_key=str(st.session_state.get("llm_api_key_input", settings.llm_api_key)),
    llm_model=str(st.session_state.get("llm_model_input", settings.llm_model)),
    llm_timeout_seconds=int(st.session_state.get("llm_timeout_input", settings.llm_timeout_seconds)),
    llm_chunk_chars=int(st.session_state.get("llm_chunk_chars_input", settings.llm_chunk_chars)),
    llm_max_chunks=int(st.session_state.get("llm_max_chunks_input", settings.llm_max_chunks)),
)
llm_client = get_llm_client(runtime_settings)

st.sidebar.title("投标项目")
new_name = st.sidebar.text_input("新项目名称", placeholder="例如：某某信息化项目")
if st.sidebar.button("新建项目", type="primary", width="stretch"):
    project = store.create_project(new_name, owner_id=current_user["id"])
    st.session_state["project_id"] = project["id"]
    st.rerun()

show_archived = st.sidebar.toggle("显示已归档项目", key="show_archived_projects")
projects = store.list_projects(
    include_archived=show_archived,
    owner_id=None if is_admin else current_user["id"],
)
project_ids = [project["id"] for project in projects]
owner_names = {item["id"]: item["display_name"] for item in auth_store.list_users()}
project_names = {}
for item in projects:
    label = f"{item['name']}（已归档）" if item["archived"] else item["name"]
    if is_admin:
        label = f"{label} · {owner_names.get(item.get('owner_id'), '未分配')}"
    project_names[item["id"]] = label
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
            imported = store.import_project_archive(backup_upload.getvalue(), owner_id=current_user["id"])
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
            duplicate = store.duplicate_project(current_id, owner_id=current_user["id"])
            st.session_state["project_id"] = duplicate["id"]
            st.session_state["project_flash"] = f"已创建项目副本：{duplicate['name']}"
            st.rerun()

        archive_label = "恢复项目" if managed_project["archived"] else "归档项目"
        if st.button(archive_label, width="stretch", key=f"archive_{current_id}"):
            store.set_project_archived(current_id, not bool(managed_project["archived"]))
            if not managed_project["archived"] and not show_archived:
                st.session_state.pop("project_id", None)
            st.rerun()

if is_admin:
    with st.sidebar.expander("模型配置", expanded=False):
        st.text_input("接口地址", value=settings.llm_base_url, key="llm_base_url_input")
        st.text_input("模型名称", value=settings.llm_model, key="llm_model_input")
        st.text_input("API Key", value=settings.llm_api_key, type="password", key="llm_api_key_input")
        parameter_columns = st.columns(2)
        parameter_columns[0].number_input(
            "分段字符数",
            min_value=4000,
            max_value=40000,
            step=1000,
            value=settings.llm_chunk_chars,
            key="llm_chunk_chars_input",
        )
        parameter_columns[1].number_input(
            "最多分段",
            min_value=1,
            max_value=20,
            step=1,
            value=settings.llm_max_chunks,
            key="llm_max_chunks_input",
        )
        st.number_input(
            "请求超时（秒）",
            min_value=30,
            max_value=600,
            step=30,
            value=settings.llm_timeout_seconds,
            key="llm_timeout_input",
        )
        action_columns = st.columns(2)
        if action_columns[0].button("检测连接", width="stretch", key="check_llm_connection"):
            health = llm_client.check_health()
            if health.available:
                st.success(health.message)
            else:
                st.error(health.message)
        if action_columns[1].button("保存配置", width="stretch", key="save_llm_configuration"):
            save_llm_settings(runtime_settings)
            st.success("模型配置已保存到本机 .env，当前会话立即生效。")
        if "localhost:11434" in runtime_settings.llm_base_url:
            st.caption(f"本地模式需要模型服务，并已配置模型：{runtime_settings.llm_model}")
        else:
            st.caption("云端 API Key 仅保存在本机 .env，不进入 Git 或项目备份。")

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
    f"补充附件：{workflow['attachment_files']} 个文件"
)

tab_upload, tab_analysis, tab_knowledge, tab_generate, tab_review, tab_export, tab_submission = st.tabs(
    [
        "1. 招标文件",
        "2. 分析确认",
        "3. 知识资料",
        "4. 章节生成",
        "5. 复核检查",
        "6. Word 导出",
        "7. 交付打包",
    ],
    key=f"project_tabs_{current_id}",
    on_change="rerun",
)

with tab_upload:
    st.subheader("上传并解析招标文件")
    parser_mode_label = st.radio(
        "解析方式",
        ["自动识别", "快速解析", "MinerU 增强"],
        horizontal=True,
        key=f"parser_mode_{current_id}",
        help="自动识别会先快速解析，仅在扫描 PDF 文本过少时调用 MinerU。",
    )
    parser_mode = {"自动识别": "auto", "快速解析": "native", "MinerU 增强": "mineru"}[parser_mode_label]
    uploaded = st.file_uploader(
        "支持 PDF、DOCX、TXT、Markdown",
        type=[extension.lstrip(".") for extension in sorted(TENDER_EXTENSIONS)],
        key=f"tender_{current_id}",
    )
    if st.button("保存并解析", type="primary", disabled=uploaded is None, key=f"parse_{current_id}"):
        try:
            path = store.save_source(current_id, uploaded.name, uploaded.getvalue())
            parsed = parse_document(path, mode=parser_mode, mineru_client=MinerUClient(settings))
            store.save_json(current_id, "parsed", parsed.model_dump())
            store.update_project(current_id, status="parsed")
            st.success("解析完成")
            st.rerun()
        except DocumentParseError as exc:
            st.error(str(exc))

    parsed_payload = store.load_json(current_id, "parsed")
    if parsed_payload:
        parsed = ParsedDocument.model_validate(parsed_payload)
        columns = st.columns(4)
        columns[0].metric("字符数", parsed.char_count)
        columns[1].metric("页数", len(parsed.pages))
        columns[2].metric("扫描件风险", "是" if parsed.possible_scanned_document else "否")
        columns[3].metric("解析引擎", "MinerU" if parsed.parser_engine == "mineru" else "原生")
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
                llm_requested = mode == "LLM 增强"
                health = llm_client.check_health() if llm_requested else None
                llm_ready = bool(health and health.available)
                analysis = analyze_document(parsed, llm_client, use_llm=llm_ready)
                if llm_requested and health and not health.available:
                    analysis.warnings.insert(0, f"未调用 LLM：{health.message} 已使用规则模式。")
                store.save_json(current_id, "analysis", analysis.model_dump())
                store.save_json(current_id, "analysis_baseline", analysis.model_dump())
                store.save_json(
                    current_id,
                    "analysis_baseline_meta",
                    {"origin": "自动分析", "source_fingerprint": _parsed_fingerprint(parsed)},
                )
                store.delete_json(current_id, "analysis_acceptance")
                store.delete_json(current_id, "review")
                store.update_project(current_id, status="analysis_pending_confirmation")
                _sync_analysis_editor_state(current_id, analysis)
            st.success("分析完成，请逐项确认")
            st.rerun()

        analysis = _load_analysis(store, current_id)
        if analysis:
            for warning in analysis.warnings:
                st.warning(warning)
            st.caption(f"分析模式：{analysis.analysis_mode}")
            st.subheader("项目基本信息")
            info_columns = st.columns(2)
            source_help = "仅根据招标文件原文填入；原文未明确出现时保持为空，需人工补充确认。"
            project_name = info_columns[0].text_input(
                "项目名称",
                analysis.project_info.project_name,
                key=f"info_name_{current_id}",
                help=source_help,
            )
            purchaser = info_columns[1].text_input(
                "招标人/采购人",
                analysis.project_info.purchaser,
                key=f"info_purchaser_{current_id}",
                help=source_help,
            )
            agency = info_columns[0].text_input(
                "代理机构",
                analysis.project_info.agency,
                key=f"info_agency_{current_id}",
                help=source_help,
            )
            budget = info_columns[1].text_input(
                "预算/最高限价",
                analysis.project_info.budget,
                key=f"info_budget_{current_id}",
                help=source_help,
            )
            deadline = st.text_input(
                "投标截止时间",
                analysis.project_info.bid_deadline,
                key=f"info_deadline_{current_id}",
                help=source_help,
            )

            mandatory = _requirement_editor("强制要求", analysis.mandatory_requirements, f"mandatory_{current_id}")
            scoring = _scoring_editor(analysis.scoring_items, f"scoring_{current_id}")
            with st.expander("资格、材料、时间和风险", expanded=False):
                qualifications = _requirement_editor("资格要求", analysis.qualification_requirements, f"qualification_{current_id}")
                documents = _requirement_editor("所需材料", analysis.required_documents, f"documents_{current_id}")
                deadlines = _requirement_editor("时间节点", analysis.deadlines, f"deadlines_{current_id}")
                risks = _requirement_editor("废标风险", analysis.risks, f"risks_{current_id}")

            edited_analysis = analysis.model_copy(deep=True)
            edited_analysis.project_info = ProjectInfo(
                project_name=project_name,
                purchaser=purchaser,
                agency=agency,
                budget=budget,
                bid_deadline=deadline,
            )
            edited_analysis.mandatory_requirements = mandatory
            edited_analysis.scoring_items = scoring
            edited_analysis.qualification_requirements = qualifications
            edited_analysis.required_documents = documents
            edited_analysis.deadlines = deadlines
            edited_analysis.risks = risks
            analysis_has_unsaved_changes = edited_analysis.model_dump() != analysis.model_dump()

            if st.button("保存人工确认结果", type="primary", key=f"save_analysis_{current_id}"):
                store.save_json(current_id, "analysis", edited_analysis.model_dump())
                store.delete_json(current_id, "analysis_acceptance")
                store.delete_json(current_id, "review")
                store.update_project(current_id, name=project_name or project["name"], status="analysis_confirmed")
                st.success("确认结果已保存")
                st.rerun()

            st.divider()
            st.subheader("真实项目分析验收")
            baseline_payload = store.load_json(current_id, "analysis_baseline")
            if not baseline_payload:
                st.info("当前项目没有自动分析基线。重新分析可生成完整基线，也可从当前结果开始记录后续变化。")
                if analysis_has_unsaved_changes:
                    st.warning("分析表格存在未保存修改，请先保存人工确认结果。")
                if st.button(
                    "以当前结果建立后续迭代基线",
                    key=f"acceptance_baseline_{current_id}",
                    disabled=analysis_has_unsaved_changes,
                ):
                    store.save_json(current_id, "analysis_baseline", analysis.model_dump())
                    store.save_json(
                        current_id,
                        "analysis_baseline_meta",
                        {
                            "origin": "历史项目当前结果",
                            "source_fingerprint": _parsed_fingerprint(parsed),
                        },
                    )
                    store.delete_json(current_id, "analysis_acceptance")
                    st.success("验收基线已建立")
                    st.rerun()
            else:
                baseline = TenderAnalysis.model_validate(baseline_payload)
                baseline_meta = store.load_json(current_id, "analysis_baseline_meta", {})
                baseline_origin = str(baseline_meta.get("origin") or "未知")
                baseline_source_fingerprint = str(baseline_meta.get("source_fingerprint") or "")
                source_matches = (
                    baseline_source_fingerprint == _parsed_fingerprint(parsed)
                    if baseline_source_fingerprint
                    else None
                )
                saved_acceptance = store.load_json(current_id, "analysis_acceptance", {})
                st.caption(f"验收基线来源：{baseline_origin}")
                if baseline_origin != "自动分析":
                    st.info("该基线只统计建立之后的变化，不代表最初自动分析结果。")
                acceptance_columns = st.columns([1, 1, 1])
                reviewer = acceptance_columns[0].text_input(
                    "验收人",
                    value=str(saved_acceptance.get("reviewer", "")),
                    key=f"acceptance_reviewer_{current_id}",
                )
                manual_minutes = acceptance_columns[1].number_input(
                    "纯人工预计耗时（分钟）",
                    min_value=0.0,
                    step=5.0,
                    value=float(saved_acceptance.get("manual_minutes") or 0),
                    key=f"acceptance_manual_minutes_{current_id}",
                )
                assisted_minutes = acceptance_columns[2].number_input(
                    "系统协助实际耗时（分钟）",
                    min_value=0.0,
                    step=5.0,
                    value=float(saved_acceptance.get("assisted_minutes") or 0),
                    key=f"acceptance_assisted_minutes_{current_id}",
                )
                acceptance_notes = st.text_area(
                    "验收备注",
                    value=str(saved_acceptance.get("notes", "")),
                    placeholder="记录漏项原因、误报类型和本轮人工处理情况",
                    key=f"acceptance_notes_{current_id}",
                )
                acceptance = build_analysis_acceptance(
                    baseline,
                    edited_analysis,
                    baseline_origin=baseline_origin,
                    source_matches=source_matches,
                    source_filename=parsed.filename,
                    reviewer=reviewer,
                    manual_minutes=manual_minutes,
                    assisted_minutes=assisted_minutes,
                    notes=acceptance_notes,
                )

                acceptance_metrics = st.columns(5)
                acceptance_metrics[0].metric("自动提取", acceptance["baseline_count"])
                acceptance_metrics[1].metric("确认命中", acceptance["accepted_count"])
                acceptance_metrics[2].metric("误报/删除", acceptance["rejected_count"])
                acceptance_metrics[3].metric("人工补充", acceptance["manual_addition_count"])
                acceptance_metrics[4].metric("待确认", acceptance["pending_count"])

                quality_metrics = st.columns(4)
                hit_rate = acceptance["reviewed_hit_rate_percent"]
                coverage = acceptance["estimated_coverage_percent"]
                time_saved = acceptance["time_saved_minutes"]
                quality_metrics[0].metric(
                    "已审核项命中率",
                    "待复核" if hit_rate is None else f"{hit_rate:.1f}%",
                )
                quality_metrics[1].metric(
                    "覆盖率估算",
                    "待复核" if coverage is None else f"{coverage:.1f}%",
                )
                quality_metrics[2].metric("人工修改", acceptance["edited_count"])
                quality_metrics[3].metric(
                    "节省时间",
                    "未填写" if time_saved is None else f"{time_saved:.1f} 分钟",
                )

                if source_matches is False:
                    st.error("当前解析结果与验收基线不一致，请重新执行分析后再验收。")
                elif acceptance["complete"]:
                    st.success("人工复核已完成，可将本项目计入真实文件验收样本。")
                else:
                    st.warning("当前仍是阶段性验收样本，请完成待确认和待核对项。")
                if analysis_has_unsaved_changes:
                    st.warning("分析表格存在未保存修改，验收记录保存和报告下载已暂停。")

                detail_rows = []
                for result_label, key in (
                    ("确认命中", "accepted_items"),
                    ("误报/删除", "rejected_items"),
                    ("人工补充", "manual_items"),
                    ("待确认", "pending_items"),
                ):
                    detail_rows.extend(
                        {
                            "结果": result_label,
                            "类别": item["category"],
                            "内容": item["content"],
                            "页码": item["source_page"],
                            "处理": item["result"],
                        }
                        for item in acceptance[key]
                    )
                if detail_rows:
                    with st.expander(f"查看验收明细（{len(detail_rows)}）"):
                        _display_chinese_table(
                            detail_rows,
                            [("结果", "结果"), ("类别", "类别"), ("内容", "内容"), ("页码", "页码"), ("处理", "处理")],
                        )

                acceptance_report = build_acceptance_report(acceptance)
                acceptance_actions = st.columns([1, 1, 3])
                acceptance_actions_disabled = analysis_has_unsaved_changes or source_matches is False
                if acceptance_actions[0].button(
                    "保存验收记录",
                    key=f"save_acceptance_{current_id}",
                    icon=":material/save:",
                    width="stretch",
                    disabled=acceptance_actions_disabled,
                ):
                    store.save_json(current_id, "analysis_acceptance", acceptance)
                    st.success("验收记录已保存")
                acceptance_actions[1].download_button(
                    "下载验收报告",
                    data=acceptance_report,
                    file_name=safe_filename(f"{analysis.project_info.project_name or project['name']}_分析验收报告.txt"),
                    mime="text/plain;charset=utf-8",
                    key=f"download_acceptance_{current_id}",
                    icon=":material/download:",
                    width="stretch",
                    disabled=acceptance_actions_disabled,
                )

with tab_knowledge:
    st.subheader("项目知识资料")
    st.caption("第一版只保留企业资料、产品资料、历史方案三类，避免知识库结构过度复杂。")

    st.markdown("#### Word 模板")
    template = store.word_template_path(current_id)
    template_upload = st.file_uploader(
        "上传甲方或公司 DOCX 模板",
        type=["docx"],
        key=(
            f"word_template_upload_{current_id}_"
            f"{st.session_state.get(f'word_template_upload_nonce_{current_id}', 0)}"
        ),
        help="支持 {{PROJECT_NAME}}、{{PURCHASER}}、{{AGENCY}}、{{BUDGET}}、{{BID_DEADLINE}}、{{GENERATED_DATE}}、{{BID_CONTENT}}。",
    )
    template_actions = st.columns([2, 2, 5])
    if template_actions[0].button(
        "保存模板",
        disabled=template_upload is None,
        key=f"save_word_template_{current_id}",
        icon=":material/upload_file:",
    ):
        try:
            saved_template = store.save_word_template(current_id, template_upload.name, template_upload.getvalue())
            nonce_key = f"word_template_upload_nonce_{current_id}"
            st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
            st.session_state["project_flash"] = f"已保存 Word 模板：{saved_template.name}"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if template:
        template_actions[1].download_button(
            "下载模板",
            data=template.read_bytes(),
            file_name=template.name,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key=f"download_word_template_{current_id}",
            icon=":material/download:",
        )
        st.caption(f"当前模板：{template.name}")
        if st.button(
            "删除当前模板",
            key=f"delete_word_template_{current_id}",
            icon=":material/delete:",
        ):
            store.delete_word_template(current_id)
            st.session_state["project_flash"] = "已删除 Word 模板"
            st.rerun()
    else:
        st.caption("尚未上传模板，导出时使用系统默认精排版。")

    st.markdown("#### 资质图片")
    qualification_uploads = st.file_uploader(
        "上传营业执照、认证证书、资质证明图片",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=(
            f"qualification_images_{current_id}_"
            f"{st.session_state.get(f'qualification_images_nonce_{current_id}', 0)}"
        ),
    )
    if st.button(
        "保存资质图片",
        disabled=not qualification_uploads,
        key=f"save_qualification_images_{current_id}",
        icon=":material/add_photo_alternate:",
    ):
        saved_count = 0
        for item in qualification_uploads:
            try:
                store.save_qualification_image(current_id, item.name, item.getvalue())
                saved_count += 1
            except ValueError as exc:
                st.error(f"{item.name}：{exc}")
        if saved_count:
            nonce_key = f"qualification_images_nonce_{current_id}"
            st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1
            st.session_state["project_flash"] = f"已保存 {saved_count} 张资质图片"
            st.rerun()
    qualification_images = store.list_qualification_images(current_id)
    if qualification_images:
        for image_path in qualification_images:
            image_columns = st.columns([1, 5, 1])
            image_columns[0].image(str(image_path), width=88)
            image_columns[1].write(f"{image_path.name} · {image_path.stat().st_size / 1024:.1f} KB")
            if image_columns[2].button(
                "删除",
                key=f"delete_qualification_image_{current_id}_{image_path.name}",
                icon=":material/delete:",
            ):
                store.delete_qualification_image(current_id, image_path.name)
                st.session_state["project_flash"] = f"已删除资质图片：{image_path.name}"
                st.rerun()
    else:
        st.caption("暂无资质图片。上传后会在 Word 末尾自动生成两列资质证明材料。")

    st.divider()
    st.markdown("#### 检索资料")
    category = st.selectbox(
        "资料类别",
        list(CATEGORY_LABELS),
        format_func=lambda value: CATEGORY_LABELS[value],
        key=f"knowledge_category_{current_id}",
    )
    knowledge_uploads = st.file_uploader(
        "上传资料",
        type=[extension.lstrip(".") for extension in sorted(KNOWLEDGE_EXTENSIONS)],
        accept_multiple_files=True,
        key=f"knowledge_upload_{current_id}",
    )
    if st.button("保存资料", disabled=not knowledge_uploads, key=f"save_knowledge_{current_id}"):
        valid_uploads = []
        validation_errors: list[str] = []
        for item in knowledge_uploads:
            content = item.getvalue()
            try:
                parsed_knowledge = parse_document_bytes(item.name, content)
                if not parsed_knowledge.full_text.strip():
                    raise DocumentParseError("没有提取到可检索文本。")
            except DocumentParseError as exc:
                validation_errors.append(f"{item.name}：{exc}")
                continue
            valid_uploads.append((item.name, content))

        for filename, content in valid_uploads:
            store.save_knowledge_file(current_id, category, filename, content)
        if valid_uploads:
            clear_knowledge_cache()
            st.success(f"已保存 {len(valid_uploads)} 个文件")
        for message in validation_errors:
            st.error(message)
        if valid_uploads and not validation_errors:
            st.rerun()

    files_by_category = store.list_knowledge_files(current_id)
    pending_delete_key = f"pending_knowledge_delete_{current_id}"
    for category_id, paths in files_by_category.items():
        with st.expander(f"{CATEGORY_LABELS[category_id]}（{len(paths)}）", expanded=bool(paths)):
            if paths:
                for path in paths:
                    reference = f"{category_id}/{path.name}"
                    file_columns = st.columns([6, 1])
                    file_columns[0].write(f"{path.name} · {path.stat().st_size / 1024:.1f} KB")
                    if file_columns[1].button(
                        "删除",
                        key=f"request_delete_knowledge_{current_id}_{reference}",
                        icon=":material/delete:",
                        help=f"删除 {path.name}",
                    ):
                        st.session_state[pending_delete_key] = reference
                        st.rerun()

                    if st.session_state.get(pending_delete_key) == reference:
                        st.warning(f"确认删除“{path.name}”？删除后无法从系统内恢复。")
                        confirm_columns = st.columns([1, 1, 4])
                        if confirm_columns[0].button(
                            "确认删除",
                            type="primary",
                            key=f"confirm_delete_knowledge_{current_id}_{reference}",
                        ):
                            deleted = store.delete_knowledge_file(current_id, reference)
                            st.session_state.pop(pending_delete_key, None)
                            if deleted:
                                clear_knowledge_cache()
                                st.session_state["project_flash"] = f"已删除知识资料：{path.name}"
                            else:
                                st.session_state["project_flash"] = f"资料已不存在：{path.name}"
                            st.rerun()
                        if confirm_columns[1].button(
                            "取消",
                            key=f"cancel_delete_knowledge_{current_id}_{reference}",
                        ):
                            st.session_state.pop(pending_delete_key, None)
                            st.rerun()
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
            llm_requested = generation_mode == "LLM 生成"
            health = llm_client.check_health() if llm_requested else None
            llm_ready = bool(health and health.available)
            if llm_requested and health and not health.available:
                st.warning(f"未调用 LLM：{health.message} 本次自动使用规则草稿。")
            for index, plan in enumerate(selected_chapters, start=1):
                query = f"{plan.title}\n{plan.instructions}"
                evidence = search_knowledge(query, files_by_category)
                draft = generate_chapter(
                    plan,
                    analysis,
                    evidence,
                    llm_client,
                    use_llm=llm_ready,
                )
                drafts.append(draft)
                progress.progress(index / len(selected_chapters), text=f"已完成：{plan.title}")
            generated_payload = [draft.model_dump() for draft in drafts]
            store.save_json(current_id, "drafts", generated_payload)
            version_label = generation_mode if llm_ready or not llm_requested else "LLM 不可用，规则回退"
            store.save_draft_version(current_id, generated_payload, f"{version_label}生成")
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

            draft_word = build_draft_docx(analysis, edited_drafts)
            st.download_button(
                "下载当前草稿 Word",
                data=draft_word,
                file_name=safe_filename(f"{analysis.project_info.project_name or project['name']}_章节草稿.docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"download_draft_word_{current_id}",
                icon=":material/download:",
            )
            st.caption("下载内容以当前编辑框为准，无需先保存；该文件不进入正式 Word 版本记录。")

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
        _display_chinese_table(
            checklist_rows,
            [("check", "检查项"), ("status", "结果"), ("detail", "说明")],
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
        template = store.word_template_path(current_id)
        qualification_images = store.list_qualification_images(current_id)
        use_template = bool(template) and st.checkbox(
            f"使用项目模板：{template.name}",
            value=True,
            key=f"use_word_template_{current_id}",
        )
        include_internal_appendices = st.checkbox(
            "附带内部核对与复核附录",
            value=False,
            key=f"include_internal_appendices_{current_id}",
            help="仅供内部评审使用。正式投标文件建议保持关闭，复核结果仍保留在系统中。",
        )
        st.caption(f"本次将自动编排 {len(qualification_images)} 张资质图片。")
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
                export_docx(
                    target["path"],
                    analysis,
                    drafts,
                    review,
                    template_path=template if use_template else None,
                    qualification_images=qualification_images,
                    include_internal_appendices=include_internal_appendices,
                )
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
                    template_filename=template.name if use_template and template else "",
                    qualification_image_count=len(qualification_images),
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
                        "created_at": format_beijing_time(item["created_at"]),
                        "chapters": item["chapter_count"],
                        "pending": summary.get("pending", 0),
                        "high": summary.get("high", 0),
                        "note": item["note"] or "-",
                        "template": item.get("template_filename") or "系统默认",
                        "qualification_images": item.get("qualification_image_count", 0),
                    }
                )
            _display_chinese_table(
                history_rows,
                [
                    ("version", "版本"),
                    ("created_at", "生成时间"),
                    ("chapters", "章节"),
                    ("pending", "待处理"),
                    ("high", "高风险"),
                    ("note", "版本说明"),
                    ("template", "模板"),
                    ("qualification_images", "资质图"),
                ],
            )

            version_by_id = {item["id"]: item for item in versions}
            export_version_state_key = f"export_version_{current_id}"
            reset_export_version_key = f"reset_export_version_{current_id}"
            if st.session_state.pop(reset_export_version_key, False):
                st.session_state.pop(export_version_state_key, None)
            elif st.session_state.get(export_version_state_key) not in version_by_id:
                st.session_state.pop(export_version_state_key, None)
            selected_version_id = st.selectbox(
                "选择下载版本",
                options=list(version_by_id),
                format_func=lambda version_id: (
                    f"V{version_by_id[version_id]['version']:03d} | "
                    f"{version_by_id[version_id]['filename']}"
                ),
                key=export_version_state_key,
            )
            selected_version = version_by_id[selected_version_id]
            word_size, word_modified_ns = _docx_file_signature(selected_version["path"])
            word_quality = _verify_docx_cached(
                str(selected_version["path"]),
                selected_version["sha256"],
                selected_version["chapter_count"],
                bool(selected_version.get("template_filename")),
                word_size,
                word_modified_ns,
            )
            if word_quality["valid"]:
                if word_quality["warnings"]:
                    st.warning(
                        f"Word 文件完整，但有 {len(word_quality['warnings'])} 个版式或内容提示项。"
                    )
                else:
                    st.success(
                        f"Word 成品质检通过：{word_quality['chapter_count']} 个正文章节，"
                        f"{word_quality['table_count']} 个表格。"
                    )
            else:
                st.error("Word 成品质检不通过，已停止该版本下载。")
                _display_chinese_table(
                    [{"issue": item} for item in word_quality["errors"]],
                    [("issue", "质检错误")],
                )
            if word_quality["warnings"]:
                with st.expander(f"查看质检提示（{len(word_quality['warnings'])}）"):
                    _display_chinese_table(
                        [{"issue": item} for item in word_quality["warnings"]],
                        [("issue", "提示项")],
                    )

            quality_report = build_docx_quality_report(
                word_quality,
                word_version=selected_version,
            )
            word_download_columns = st.columns([1, 1, 1, 2])
            word_download_columns[0].download_button(
                "下载所选 Word",
                data=selected_version["path"].read_bytes() if word_quality["valid"] else b"",
                file_name=selected_version["filename"],
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                disabled=not word_quality["valid"],
                width="stretch",
                icon=":material/download:",
            )
            word_download_columns[1].download_button(
                "下载质检报告",
                data=quality_report,
                file_name=safe_filename(f"{Path(selected_version['filename']).stem}_质检报告.txt"),
                mime="text/plain;charset=utf-8",
                width="stretch",
                icon=":material/fact_check:",
            )
            pending_export_delete_key = f"pending_export_delete_{current_id}"
            if word_download_columns[2].button(
                "删除此版本",
                key=f"request_delete_export_{current_id}_{selected_version_id}",
                icon=":material/delete:",
                width="stretch",
            ):
                st.session_state[pending_export_delete_key] = selected_version_id

            if st.session_state.get(pending_export_delete_key) == selected_version_id:
                st.warning(
                    f"确认删除 V{selected_version['version']:03d}？Word 文件和版本记录将被永久删除，"
                    "已经生成的交付包不受影响。"
                )
                delete_columns = st.columns([1, 1, 4])
                if delete_columns[0].button(
                    "确认删除",
                    type="primary",
                    key=f"confirm_delete_export_{current_id}_{selected_version_id}",
                ):
                    deleted = store.delete_export_version(current_id, selected_version_id)
                    st.session_state.pop(pending_export_delete_key, None)
                    st.session_state[reset_export_version_key] = True
                    _verify_docx_cached.clear()
                    st.session_state["project_flash"] = (
                        f"已删除 Word V{selected_version['version']:03d}"
                        if deleted
                        else "该 Word 版本已不存在"
                    )
                    st.rerun()
                if delete_columns[1].button(
                    "取消",
                    key=f"cancel_delete_export_{current_id}_{selected_version_id}",
                ):
                    st.session_state.pop(pending_export_delete_key, None)
                    st.rerun()
            st.caption(
                f"实际 SHA-256：{word_quality['sha256']} · "
                f"{word_quality['size'] / 1024 / 1024:.2f} MB · "
                f"质检时间：{format_beijing_time(word_quality['verified_at'])}"
            )
        else:
            st.caption("尚未生成 Word 版本。")


with tab_submission:
    st.subheader("交付打包")
    export_versions = store.list_export_versions(current_id)
    attachment_files = store.list_attachment_files(current_id)
    package_versions = store.list_package_versions(current_id)
    summary_columns = st.columns(3)
    summary_columns[0].metric("Word 版本", len(export_versions))
    summary_columns[1].metric("补充附件", sum(len(paths) for paths in attachment_files.values()))
    summary_columns[2].metric("交付包", len(package_versions))

    with st.expander("补充附件（可选）", expanded=False):
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
            st.session_state["project_flash"] = f"已保存 {len(attachment_uploads)} 个补充附件"
            st.rerun()

        attachment_files = store.list_attachment_files(current_id)
        if not any(attachment_files.values()):
            st.caption("暂无补充附件，本次将只打包所选 Word。")
        for category_id, label in ATTACHMENT_CATEGORY_LABELS.items():
            paths = attachment_files[category_id]
            if not paths:
                continue
            with st.expander(f"{label}（{len(paths)}）", expanded=False):
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

    st.markdown("#### 1. 选择 Word 成品")
    selected_word_version = None
    selected_word_quality = None
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
        selected_word_size, selected_word_modified_ns = _docx_file_signature(
            selected_word_version["path"]
        )
        selected_word_quality = _verify_docx_cached(
            str(selected_word_version["path"]),
            selected_word_version["sha256"],
            selected_word_version["chapter_count"],
            bool(selected_word_version.get("template_filename")),
            selected_word_size,
            selected_word_modified_ns,
        )
    else:
        st.info("请先在“6. Word 导出”中生成至少一个 Word 版本。")

    st.markdown("#### 2. 生成交付包")
    attachment_files = store.list_attachment_files(current_id)
    attachment_refs = {
        f"{ATTACHMENT_CATEGORY_LABELS[category_id]}/{path.name}"
        for category_id, paths in attachment_files.items()
        for path in paths
    }
    inventory_items = build_attachment_inventory(attachment_files)
    package_rows = []
    if selected_word_version:
        package_rows.append(
            {
                "category": "投标文件",
                "filename": selected_word_version["filename"],
                "size": f"{selected_word_version['size'] / 1024:.1f} KB",
            }
        )
    package_rows.extend(
        {
            "category": ATTACHMENT_CATEGORY_LABELS[category_id],
            "filename": path.name,
            "size": f"{path.stat().st_size / 1024:.1f} KB",
        }
        for category_id, paths in attachment_files.items()
        for path in paths
    )
    if package_rows:
        _display_chinese_table(
            package_rows,
            [("category", "类别"), ("filename", "本次包内容"), ("size", "大小")],
        )

    review = _load_review(store, current_id)
    package_readiness = build_package_readiness(
        selected_word_version,
        inventory_items,
        attachment_refs,
        review,
        word_quality=selected_word_quality,
    )
    package_status_labels = {"pass": "通过", "warning": "需确认", "block": "不可生成"}
    _display_chinese_table(
        [
            {
                "check": item["label"],
                "status": package_status_labels[item["status"]],
                "detail": item["detail"],
            }
            for item in package_readiness["checks"]
        ],
        [("check", "检查项"), ("status", "结果"), ("detail", "说明")],
    )

    package_acknowledged = not package_readiness["requires_confirmation"]
    if package_readiness["blocking_count"]:
        st.error("Word 尚未准备完成，暂不能生成交付包。")
    if package_readiness["requires_confirmation"]:
        package_acknowledged = st.checkbox(
            "我已知悉复核风险，本次生成包仅用于内部预审",
            key=f"package_ack_{current_id}",
        )

    package_note = st.text_input(
        "版本说明",
        placeholder="例如：第一次内部预审",
        key=f"package_note_{current_id}",
    )
    if st.button(
        "生成交付包",
        type="primary",
        key=f"create_package_{current_id}",
        disabled=not package_readiness["can_package"] or not package_acknowledged,
        icon=":material/folder_zip:",
    ):
        with st.spinner("正在核对文件并生成交付包..."):
            target = store.next_package_version(
                current_id,
                safe_filename(f"{project['name']}_交付包.zip"),
            )
            create_submission_package(
                target["path"],
                project=project,
                word_version=selected_word_version,
                items=inventory_items,
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
        st.session_state["project_flash"] = f"交付包 P{target['version']:03d} 已生成"
        st.rerun()

    package_versions = store.list_package_versions(current_id)
    if package_versions:
        st.markdown("##### 交付包版本记录")
        _display_chinese_table(
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
            [
                ("version", "版本"),
                ("created_at", "生成时间"),
                ("word", "Word"),
                ("attachments", "附件"),
                ("warnings", "风险确认"),
                ("scope", "用途"),
                ("note", "版本说明"),
            ],
        )
        package_by_id = {item["id"]: item for item in package_versions}
        selected_package_id = st.selectbox(
            "选择下载交付包版本",
            options=list(package_by_id),
            format_func=lambda version_id: (
                f"P{package_by_id[version_id]['version']:03d} | "
                f"{package_by_id[version_id]['filename']}"
            ),
            key=f"package_version_{current_id}",
        )
        selected_package = package_by_id[selected_package_id]
        package_stat = selected_package["path"].stat()
        verification = _verify_package_cached(
            str(selected_package["path"]),
            selected_package["sha256"],
            package_stat.st_size,
            package_stat.st_mtime_ns,
        )
        if verification["valid"]:
            st.success(
                f"完整性校验通过：{verification['file_count']} 个内容文件，"
                f"{verification['content_size'] / 1024 / 1024:.2f} MB。"
            )
        else:
            st.error("完整性校验不通过，已停止交付包下载。")
            _display_chinese_table(
                [{"issue": item} for item in verification["errors"]],
                [("issue", "校验错误")],
            )
        if verification["warnings"]:
            st.warning("；".join(verification["warnings"]))

        verification_report = build_package_verification_report(
            verification,
            package_version=selected_package,
        )
        download_columns = st.columns([1, 1, 3])
        download_columns[0].download_button(
            "下载所选交付包",
            data=selected_package["path"].read_bytes() if verification["valid"] else b"",
            file_name=selected_package["filename"],
            mime="application/zip",
            disabled=not verification["valid"],
            width="stretch",
            icon=":material/download:",
        )
        download_columns[1].download_button(
            "下载校验报告",
            data=verification_report,
            file_name=safe_filename(f"{Path(selected_package['filename']).stem}_校验报告.txt"),
            mime="text/plain;charset=utf-8",
            width="stretch",
            icon=":material/verified:",
        )
        st.caption(
            f"实际 SHA-256：{verification['sha256']} · "
            f"{verification['size'] / 1024 / 1024:.2f} MB · "
            f"校验时间：{verification['verified_at'].replace('T', ' ').replace('+00:00', ' UTC')}"
        )
    else:
        st.caption("尚未生成交付包版本。")
