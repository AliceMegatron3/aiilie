"""models/quantification.py — 量化/解析/文档版本显式数据模型（Batch 2 收尾）。

此前这些来源记录散落在任务 extra/裸 dict，未形成可复用契约。本模块用显式
Pydantic 字段承载 provenance（run_id/source/input_hash/parser/model/artifact/
reviewer/replay 等），不依赖 `extra=allow` 透传。供量化链、回放与审计复用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ParseRun(BaseModel):
    """一次文档解析/拆书的执行记录（provenance：来源、工具、版本、产物）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="解析运行标识")
    document_id: str = Field(..., description="被解析文档/书 id")
    source_revision: str = Field(default="", description="来源修订/版本标识")
    parser: str = Field(default="", description="解析器 id")
    doc_version: int = Field(default=1, ge=1, description="产出文档版本号")
    status: Literal["RUNNING", "COMPLETED", "FAILED"] = Field(default="RUNNING")
    chars_parsed: int = Field(default=0, ge=0)
    pages: int = Field(default=0, ge=0)
    output_format: str = Field(default="blocks", description="解析输出形态，如 blocks/markdown")
    manifest: dict[str, Any] = Field(default_factory=dict, description="解析产物 manifest")
    error_message: str = Field(default="")
    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = Field(default=None)


class QuantifyRun(BaseModel):
    """一次量化评估的执行记录（汇入唯一 ReflectionSession 的事实源）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="量化运行标识")
    quantize_round: int = Field(default=1, ge=1, description="量化轮次")
    source_document_id: str = Field(..., description="来源文档/段落归属")
    input_hash: str = Field(default="", description="输入指纹（幂等/回放去重用）")
    parser: str = Field(default="", description="解析器 id")
    model: str = Field(default="", description="模型 id（空=纯规则/无 LLM）")
    status: Literal["COMPLETED", "FAILED", "NEEDS_REVIEW"] = Field(default="COMPLETED")
    claims: list[str] = Field(default_factory=list, description="本次产生的主张 id")
    evidence_refs: list[str] = Field(default_factory=list, description="绑定的证据 id")
    metrics: dict[str, Any] = Field(default_factory=dict, description="量化指标快照")
    artifact: dict[str, Any] = Field(default_factory=dict, description="关联产物汇总")
    reviewer: str = Field(default="", description="审核人（作者/评审）")
    replay: bool = Field(default=False, description="是否为回放/重放产生的快照")
    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = Field(default=None)


class DocumentVersion(BaseModel):
    """文档的版本化快照（与 ParseRun 关联，可回放/审计）。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(..., ge=1, description="版本号（自增）")
    document_id: str = Field(..., description="所属文档 id")
    snapshot_hash: str = Field(..., description="内容指纹（幂等/变更判定）")
    source_run_id: str = Field(default="", description="产生本版本的解析运行 id")
    parser: str = Field(default="", description="解析器 id")
    manifest: dict[str, Any] = Field(default_factory=dict, description="版本 manifest")
    created_by: str = Field(default="", description="创建者/来源")
    created_at: datetime = Field(default_factory=_now)


__all__ = ["ParseRun", "QuantifyRun", "DocumentVersion"]