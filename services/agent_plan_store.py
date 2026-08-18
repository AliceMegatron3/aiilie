"""Agent Plan 持久化 store（V0.4 受控执行 — 计划模式落库）。

仿照 ScratchpadManager 的文件态落库 + safe_join 防路径遍历：
每个 plan 序列化为一个 JSON 文件（plan_id 为文件名），读写均在受控目录内。
持久化用 AgentPlan.to_dict/from_dict，审批门不变量在反序列化后保持。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from core.path_resolver import get_app_data_dir, safe_join
from services.agent_plan import AgentPlan

logger = logging.getLogger(__name__)


class AgentPlanStore:
    """文件态 Agent Plan 存储（目录：%APPDATA%/No0_AI_V4/data/agent_plans）。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.dir = base_dir or (get_app_data_dir() / "agent_plans")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, plan_id: str) -> Path:
        # safe_join 防 plan_id 路径遍历读写
        return safe_join(self.dir, f"{plan_id}.json")

    async def save(self, plan: AgentPlan) -> None:
        path = self._path(plan.plan_id)
        raw = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)
        await asyncio.to_thread(path.write_text, raw, encoding="utf-8")

    async def load(self, plan_id: str) -> AgentPlan | None:
        path = self._path(plan_id)
        if not await asyncio.to_thread(path.exists):
            return None
        try:
            data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            return AgentPlan.from_dict(data)
        except Exception as exc:  # 顽健：坏文件不阻塞其它计划
            logger.error("加载 Agent Plan 失败 [%s]: %s", plan_id, exc)
            return None

    async def list_plans(self) -> list[dict[str, Any]]:
        plans: list[dict[str, Any]] = []
        if not await asyncio.to_thread(self.dir.is_dir):
            return plans
        for path in await asyncio.to_thread(lambda: list(self.dir.glob("*.json"))):
            try:
                data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
                latest = data.get("latest_version", 0)
                snapshots = data.get("snapshots", [])
                latest_status = next(
                    (s["status"] for s in reversed(snapshots) if s["version"] == latest),
                    "DRAFT",
                )
                plans.append(
                    {
                        "plan_id": data["plan_id"],
                        "workspace_id": data.get("workspace_id"),
                        "goal": next(
                            (s["goal"] for s in snapshots if s["version"] == latest), ""
                        ),
                        "latest_version": latest,
                        "status": latest_status,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("列出 Agent Plan 忽略坏文件 %s: %s", path.name, exc)
        plans.sort(key=lambda p: p["plan_id"])
        return plans

    async def delete(self, plan_id: str) -> bool:
        path = self._path(plan_id)
        if await asyncio.to_thread(path.exists):
            await asyncio.to_thread(path.unlink)
            return True
        return False


agent_plan_store = AgentPlanStore()


__all__ = ["AgentPlanStore", "agent_plan_store"]