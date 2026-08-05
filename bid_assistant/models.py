from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ParsedPage(BaseModel):
    page_number: int | None = None
    text: str = ""


class ParsedDocument(BaseModel):
    filename: str
    file_type: str
    pages: list[ParsedPage] = Field(default_factory=list)
    full_text: str = ""
    char_count: int = 0
    possible_scanned_document: bool = False
    parser_engine: str = "native"
    warnings: list[str] = Field(default_factory=list)


class ProjectInfo(BaseModel):
    project_name: str = ""
    purchaser: str = ""
    agency: str = ""
    budget: str = ""
    bid_deadline: str = ""


class RequirementItem(BaseModel):
    id: str = Field(default_factory=lambda: make_id("req"))
    category: str = "强制要求"
    content: str
    source_page: int | None = None
    source_quote: str = ""
    confidence: float = 0.6
    status: Literal["待确认", "已确认", "忽略", "待核对"] = "待确认"


class ScoringItem(BaseModel):
    id: str = Field(default_factory=lambda: make_id("score"))
    criterion: str
    points: str = ""
    response_hint: str = ""
    source_page: int | None = None
    source_quote: str = ""
    confidence: float = 0.6
    status: Literal["待确认", "已确认", "忽略", "待核对"] = "待确认"


class ChapterPlan(BaseModel):
    id: str = Field(default_factory=lambda: make_id("chapter"))
    title: str
    selected: bool = True
    instructions: str = ""
    requirement_ids: list[str] = Field(default_factory=list)


class ChapterDraft(BaseModel):
    chapter_id: str
    title: str
    markdown: str
    evidence_sources: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)


class KnowledgeChunk(BaseModel):
    category: str
    source_file: str
    text: str
    score: float = 0


class ReviewIssue(BaseModel):
    id: str = Field(default_factory=lambda: make_id("issue"))
    severity: Literal["高", "中", "低"]
    category: str
    message: str
    suggestion: str = ""
    related_id: str = ""
    source_page: int | None = None
    status: Literal["待处理", "已处理", "忽略"] = "待处理"


class ReviewReport(BaseModel):
    issues: list[ReviewIssue] = Field(default_factory=list)
    generated_at: str = Field(default_factory=utc_now_iso)

    def severity_count(self, severity: Literal["高", "中", "低"]) -> int:
        return sum(item.severity == severity and item.status == "待处理" for item in self.issues)

    def pending_count(self) -> int:
        return sum(item.status == "待处理" for item in self.issues)


class SubmissionItem(BaseModel):
    id: str = Field(default_factory=lambda: make_id("submit"))
    category: str = "商务文件"
    name: str
    source_requirement_id: str = ""
    source_page: int | None = None
    required: bool = True
    status: Literal["待准备", "已备妥", "不适用"] = "待准备"
    attachment: str = ""
    note: str = ""


class TenderAnalysis(BaseModel):
    project_info: ProjectInfo = Field(default_factory=ProjectInfo)
    mandatory_requirements: list[RequirementItem] = Field(default_factory=list)
    scoring_items: list[ScoringItem] = Field(default_factory=list)
    qualification_requirements: list[RequirementItem] = Field(default_factory=list)
    required_documents: list[RequirementItem] = Field(default_factory=list)
    deadlines: list[RequirementItem] = Field(default_factory=list)
    risks: list[RequirementItem] = Field(default_factory=list)
    outline: list[ChapterPlan] = Field(default_factory=list)
    analysis_mode: str = "rules"
    warnings: list[str] = Field(default_factory=list)
