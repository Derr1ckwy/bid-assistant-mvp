from __future__ import annotations

from bid_assistant.models import SubmissionItem, TenderAnalysis


SUBMISSION_CATEGORIES = ["资格文件", "商务文件", "技术文件", "报价文件", "签章与装订", "其他"]


def infer_submission_category(content: str) -> str:
    text = content.lower()
    if any(keyword in text for keyword in ("报价", "分项报价", "开标一览", "价格", "投标函")):
        return "报价文件"
    if any(keyword in text for keyword in ("技术", "参数", "方案", "实施", "服务", "响应表")):
        return "技术文件"
    if any(keyword in text for keyword in ("营业执照", "资质", "证书", "社保", "身份证", "业绩", "合同")):
        return "资格文件"
    if any(keyword in text for keyword in ("签字", "盖章", "密封", "装订", "授权委托", "法定代表人")):
        return "签章与装订"
    return "商务文件"


def sync_submission_items(
    analysis: TenderAnalysis,
    existing: list[SubmissionItem],
) -> list[SubmissionItem]:
    existing_by_source = {
        item.source_requirement_id: item
        for item in existing
        if item.source_requirement_id
    }
    synced: list[SubmissionItem] = []
    for requirement in analysis.required_documents:
        if requirement.status == "忽略":
            continue
        current = existing_by_source.get(requirement.id)
        if current:
            synced.append(
                current.model_copy(
                    update={
                        "name": requirement.content,
                        "source_page": requirement.source_page,
                        "required": True,
                    }
                )
            )
        else:
            synced.append(
                SubmissionItem(
                    category=infer_submission_category(requirement.content),
                    name=requirement.content,
                    source_requirement_id=requirement.id,
                    source_page=requirement.source_page,
                )
            )

    synced.extend(item for item in existing if not item.source_requirement_id)
    return synced


def summarize_submission_items(items: list[SubmissionItem], attachment_refs: set[str]) -> dict[str, int | bool]:
    required_items = [item for item in items if item.required]
    pending_required = sum(item.status == "待准备" for item in required_items)
    linked = sum(bool(item.attachment) for item in items)
    broken_links = sum(bool(item.attachment) and item.attachment not in attachment_refs for item in items)
    ready = sum(item.status == "已备妥" for item in items)
    not_applicable = sum(item.status == "不适用" for item in items)
    return {
        "total": len(items),
        "required": len(required_items),
        "ready": ready,
        "not_applicable": not_applicable,
        "pending_required": pending_required,
        "linked": linked,
        "broken_links": broken_links,
        "complete": bool(items) and pending_required == 0,
    }
