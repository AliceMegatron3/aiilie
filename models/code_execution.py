"""受限编程执行域数据契约。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class CodeFileSpec(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str | None = None
    expected_digest: str | None = None


class CodePlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: _id("codeplan"))
    workspace_id: str = "default"
    language: Literal["python"] = "python"
    files_to_read: list[str] = Field(default_factory=list)
    files_to_write: list[str] = Field(default_factory=list)
    argv: list[str] = Field(default_factory=list)
    cwd_relative: str = ""
    capabilities: list[str] = Field(default_factory=list)
    network_policy: Literal["deny"] = "deny"
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    max_output_bytes: int = Field(default=262144, ge=1024, le=4 * 1024 * 1024)
    resource_profile: Literal["auto", "low", "standard", "high"] = "auto"
    disk_budget_bytes: int = Field(default=0, ge=0)
    max_memory_mb: int = Field(default=512, ge=64, le=16384)
    max_processes: int = Field(default=8, ge=1, le=32)
    cpu_time_seconds: int = Field(default=120, ge=1, le=1800)
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_approval: bool = True
    approval_id: str = ""
    status: Literal["PLANNED", "APPROVED", "REJECTED"] = "PLANNED"
    created_at: str = Field(default_factory=_now)


class CodeTaskRequest(BaseModel):
    workspace_id: str = "default"
    command: Literal["compileall", "pytest", "unittest"]
    target: str = "."
    cwd_relative: str = ""
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    max_output_bytes: int = Field(default=262144, ge=1024, le=4 * 1024 * 1024)
    resource_profile: Literal["auto", "low", "standard", "high"] = "auto"
    approval_id: str | None = None


class CodeArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: _id("artifact"))
    task_id: str
    kind: Literal["stdout", "stderr", "test_report", "build_output"]
    path: str = ""
    content_digest: str = ""
    size_bytes: int = 0
    created_at: str = Field(default_factory=_now)




class CodeAudit(BaseModel):
    run_id: str = Field(default_factory=lambda: _id("codeaudit"))
    task_id: str
    plan_id: str = ""
    approval_id: str = ""
    policy_version: str = "code-policy-v1"
    command_digest: str = ""
    workspace_snapshot: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    sandbox_profile: str = "workspace-staging-no-network"
    network_policy: str = "deny"
    resource_requested: dict[str, Any] = Field(default_factory=dict)
    resource_observed: dict[str, Any] = Field(default_factory=dict)
    exit_code: int | None = None
    stdout_digest: str = ""
    stderr_digest: str = ""
    changed_files: list[str] = Field(default_factory=list)
    status: str = "PLANNED"
    created_at: str = Field(default_factory=_now)


class CodeTaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CodeTaskPhase(str, Enum):
    PLANNING = "PLANNING"
    STAGING = "STAGING"
    EXECUTING = "EXECUTING"
    FINALIZING = "FINALIZING"


class CodeTaskRecord(BaseModel):
    task_id: str
    plan_id: str
    status: CodeTaskStatus = CodeTaskStatus.PENDING
    phase: CodeTaskPhase = CodeTaskPhase.PLANNING
    progress: int = Field(default=0, ge=0, le=100)
    message: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    result: dict[str, Any] | None = None
    error: str = ""
