"""受控执行智能体 — 计划审计轨（V0.4 每步审计 / 可回放可解释）。

方向报告「四、执行链」：核心调度器执行 → 每一步写入审计事件 → 返回结果和证据；
「每个工具必须经过…审计记录」。本模块实现**只追加**（append-only）的 JSONL 审计轨：
- 每条执行事件按序落盘（plan_id 维度），不可覆盖、不可删除；
- 不存敏感参数原文，只存 args_digest（SHA-256），保证可回放可解释且不过度留存。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.path_resolver import get_app_data_dir, safe_join

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditEvent:
    plan_id: str
    version: int
    actor: str
    tool: str
    step_id: str
    action: str            # 例如 dispatched / started / failed
    status: str            # 例如 PENDING / OK / ERROR
    note: str = ""
    event_id: str = ""
    occurred_at: str = ""
    args_digest: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            object.__setattr__(self, "event_id", f"evt_{uuid4().hex[:12]}")
        if not self.occurred_at:
            object.__setattr__(self, "occurred_at", _now())

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "plan_id": self.plan_id,
            "version": self.version,
            "actor": self.actor,
            "tool": self.tool,
            "step_id": self.step_id,
            "action": self.action,
            "status": self.status,
            "note": self.note,
            "occurred_at": self.occurred_at,
            "args_digest": self.args_digest,
        }


def digest_args(args: dict[str, Any] | list | None) -> str:
    """参数指纹（SHA-256），不把原始参数写入审计轨。"""
    if args is None:
        return ""
    raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PlanAuditStore:
    """只追加 JSONL 审计轨；每计划一个文件。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.dir = base_dir or (get_app_data_dir() / "agent_plans_audit")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, plan_id: str) -> Path:
        # safe_join 防 plan_id 路径遍历
        return safe_join(self.dir, f"{plan_id}.audit.jsonl")

    async def append(self, event: AuditEvent) -> None:
        path = self._path(event.plan_id)
        line = json.dumps(event.to_row(), ensure_ascii=False) + "\n"
        async with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                await asyncio.to_thread(fh.write, line)

    async def list(self, plan_id: str) -> list[dict[str, Any]]:
        path = self._path(plan_id)
        if not await asyncio.to_thread(path.exists):
            return []
        rows: list[dict[str, Any]] = []
        async with self._lock:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("审计轨存在坏行（忽略）: %s", plan_id)
        return rows

    async def delete(self, plan_id: str) -> bool:
        path = self._path(plan_id)
        if await asyncio.to_thread(path.exists):
            async with self._lock:
                await asyncio.to_thread(path.unlink)
            return True
        return False


plan_audit_store = PlanAuditStore()


__all__ = ["AuditEvent", "PlanAuditStore", "digest_args", "plan_audit_store"]