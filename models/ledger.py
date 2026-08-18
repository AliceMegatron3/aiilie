"""统一知识 Ledger 的最小数据契约。

这些对象描述来源、证据、命题、指标和关系；前端卡片只是它们的兼容投影。
所有记录都带版本/状态字段，便于后续迁移到更严格的治理流水线。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class DocumentRecord(BaseModel):
    document_id: str
    title: str = ""
    source_uri: str = ""
    media_type: str = "text/plain"
    content_hash: str = ""
    parser_id: str = "legacy"
    parser_version: str = ""
    license_status: str = "unknown"
    status: str = "active"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class PassageRecord(BaseModel):
    passage_id: str = Field(default_factory=lambda: _id("passage"))
    document_id: str
    sequence: int = Field(default=0, ge=0)
    heading: str = ""
    quote: str = ""
    text_hash: str = ""
    char_start: int | None = None
    char_end: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    status: str = "active"
    created_at: str = Field(default_factory=_now)


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _id("evidence"))
    document_id: str
    passage_id: str
    quote: str = ""
    anchor: dict[str, Any] = Field(default_factory=dict)
    extraction_run_id: str = ""
    evidence_level: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = "verified"
    created_at: str = Field(default_factory=_now)


class ClaimRecord(BaseModel):
    claim_id: str
    document_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    claim_type: str = "FACT"
    content: str
    fingerprint: str
    scope_level: str = "book"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = "draft"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class MetricRecord(BaseModel):
    metric_id: str
    document_id: str
    name: str
    value: Any
    unit: str = ""
    method: str = ""
    sample_size: int = Field(default=0, ge=0)
    confidence_low: float | None = None
    confidence_high: float | None = None
    semantic_level: str = "descriptive"
    source_claim_ids: list[str] = Field(default_factory=list)
    status: str = "draft"
    created_at: str = Field(default_factory=_now)


class RelationRecord(BaseModel):
    relation_id: str
    source_id: str
    target_id: str
    relation_type: str = "related_to"
    weight: float = 1.0
    note: str = ""
    status: str = "active"
    created_at: str = Field(default_factory=_now)


class RunAuditRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: _id("run"))
    stage: str
    artifact_id: str = ""
    model: str = ""
    prompt_version: str = ""
    tokens: int = 0
    duration_ms: int = 0
    status: str = "completed"
    error: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class LawRecord(BaseModel):
    """法则候选/版本的持久化契约；求解器由后续法则内核负责。"""
    law_id: str
    version: int = Field(default=1, ge=1)
    layer: str = "AUTHOR_CANON"
    name: str
    variables: dict[str, Any] = Field(default_factory=dict)
    expression: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    strength: str = "advisory"
    status: str = "draft"
    source_evidence_ids: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class LawBranchRecord(BaseModel):
    """作者世界分支；revision 只增不改，支持比较和回滚。"""
    branch_id: str
    name: str
    parent_branch_id: str | None = None
    status: str = "active"
    current_revision: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class LawBranchRevision(BaseModel):
    branch_id: str
    revision: int = Field(ge=0)
    operation: str = "snapshot"
    snapshot: dict[str, Any] = Field(default_factory=dict)
    note: str = ""
    created_at: str = Field(default_factory=_now)
