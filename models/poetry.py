"""古诗长期研究域的第一版领域契约。

原文、版本、句子和音韵结果分层保存；不把普通话读音冒充中古音，也不让歧义结果强行二值化。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class PoetryEdition(BaseModel):
    edition_id: str = Field(default_factory=lambda: _id("edition"))
    source_uri: str = ""
    source_revision: str = ""
    content_hash: str
    license_expression: str = "NOASSERTION"
    license_status: Literal["known", "unknown", "restricted"] = "unknown"
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)


class PoetryLine(BaseModel):
    line_id: str = Field(default_factory=lambda: _id("line"))
    work_id: str
    edition_id: str
    sequence: int = Field(ge=0)
    raw_text: str
    normalized_text: str
    char_start: int | None = None
    char_end: int | None = None
    punctuation_original: str = ""
    variant_chars: list[dict[str, Any]] = Field(default_factory=list)


class ProsodyReading(BaseModel):
    line_id: str
    reading_system: Literal["unknown", "mandarin", "middle_chinese", "ping_shui", "ci_lin"] = "unknown"
    readings: list[dict[str, Any]] = Field(default_factory=list)
    rhyme_group: str | None = None
    status: Literal["known", "ambiguous", "unknown"] = "unknown"
    rule_set_version: str = ""
    input_hash: str = ""


class MeterForm(BaseModel):
    form_id: str
    name: str
    line_lengths: list[int] = Field(default_factory=list)
    line_count: int | None = None
    rhyme_positions: list[int] = Field(default_factory=list)
    rule_set_version: str = "1.0"
    severity: Literal["hard", "soft", "advisory"] = "advisory"


class PoetryWork(BaseModel):
    work_id: str = Field(default_factory=lambda: _id("work"))
    title: str
    author: str = ""
    dynasty: str = ""
    genre: str = ""
    edition_ids: list[str] = Field(default_factory=list)
    line_ids: list[str] = Field(default_factory=list)
    attribution_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class PoetryEvaluation(BaseModel):
    evaluation_id: str = Field(default_factory=lambda: _id("poetryeval"))
    work_id: str
    rule_set_version: str
    input_hash: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    ambiguous_items: list[dict[str, Any]] = Field(default_factory=list)
    evaluator: str = "deterministic"
    status: Literal["draft", "reviewed", "approved"] = "draft"
    created_at: str = Field(default_factory=_now)
