"""时间线域 tool handler 测试（V0.4：timeline.event_create 真实项目能力接线）。

覆盖：
- feature.timeline_enable 关闭 → 结构化 DISABLED（fail-closed，不伪装成功）；
- 必填参数缺失 → 抛错；
- feature 开启 → 经真实 TimelineService 创建事件并持久化到项目时间线；
- 已批准计划内执行 timeline.event_create。
"""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from models.project import AuthorProject
from models.timeline import Timeline
from services.agent_plan import AgentPlan, PlanStep
from services.plan_executor import PlanExecutor
from services.plan_tools_root import build_default_handlers
from services.timeline_tools import make_timeline_event_create_handler


class FakeProjectManager:
    """TimelineService 依赖的最小替身（get_project / update_project）。"""

    def __init__(self) -> None:
        self.projects: dict[str, AuthorProject] = {}

    async def get_project(self, project_id: str):
        return self.projects.get(project_id)

    async def update_project(self, project: AuthorProject) -> None:
        self.projects[project.project_id] = project


def _enable_timeline(enable: bool):
    feature_cfg = config_manager._config.setdefault("feature", {})
    prev = feature_cfg.get("timeline_enable")
    if enable:
        feature_cfg["timeline_enable"] = True
    else:
        feature_cfg.pop("timeline_enable", None)
    return prev


def _restore_timeline(prev) -> None:
    feature_cfg = config_manager._config.setdefault("feature", {})
    if prev is None:
        feature_cfg.pop("timeline_enable", None)
    else:
        feature_cfg["timeline_enable"] = prev


def _project_with_timeline() -> FakeProjectManager:
    pm = FakeProjectManager()
    pm.projects["p1"] = AuthorProject(
        project_id="p1",
        project_name="测试项目",
        timelines=[Timeline(timeline_id="tl1", name="主线")],
    )
    return pm


@pytest.mark.asyncio
async def test_event_create_feature_disabled():
    # 显式关闭 feature.timeline_enable（config.yaml 默认开启）→ 结构化 DISABLED（不伪装成功）
    feature_cfg = config_manager._config.setdefault("feature", {})
    prev = feature_cfg.get("timeline_enable")
    feature_cfg["timeline_enable"] = False
    try:
        handler = make_timeline_event_create_handler(_project_with_timeline())
        res = await handler({"project_id": "p1", "timeline_id": "tl1", "title": "觉醒"})
        assert res["status"] == "DISABLED"
        assert res["feature"] == "timeline"
        assert res["reason"] == "feature_timeline_disabled"
    finally:
        _restore_timeline(prev)


@pytest.mark.asyncio
async def test_event_create_requires_args():
    prev = _enable_timeline(True)
    try:
        handler = make_timeline_event_create_handler(_project_with_timeline())
        with pytest.raises(ValueError, match="project_id / timeline_id / title"):
            await handler({"project_id": "p1", "timeline_id": "tl1"})
    finally:
        _restore_timeline(prev)


@pytest.mark.asyncio
async def test_event_create_real_event_persisted():
    prev = _enable_timeline(True)
    try:
        pm = _project_with_timeline()
        handler = make_timeline_event_create_handler(pm)
        res = await handler({
            "project_id": "p1", "timeline_id": "tl1", "title": "主角觉醒",
            "story_time": "第一章", "track_index": 1, "position_x": 42,
            "character_states": {"主角": "存活"}, "event_type": "REVEAL",
        })
        assert res["status"] == "OK"
        assert res["event"]["title"] == "主角觉醒"
        assert res["event"]["story_time"] == "第一章"
        assert res["event"]["track_index"] == 1
        assert res["event"]["character_states"]["主角"] == "存活"
        assert res["event"]["event_type"] == "REVEAL"
        # 已持久化到项目时间线（真实 TimelineService.add_event 路径）
        events = pm.projects["p1"].timelines[0].events
        assert len(events) == 1 and events[0].title == "主角觉醒"
    finally:
        _restore_timeline(prev)


@pytest.mark.asyncio
async def test_event_create_within_approved_plan():
    prev = _enable_timeline(True)
    try:
        pm = _project_with_timeline()
        plan = AgentPlan(workspace_id="ws", identity="agent-a", goal="记录转折点")
        v1 = plan.draft("记录转折点", [
            PlanStep(tool="timeline.event_create", args={
                "project_id": "p1", "timeline_id": "tl1", "title": "关键转折",
            }),
        ])
        plan.submit_for_review(v1, "author")
        plan.approve(v1, "author")
        results = await PlanExecutor().execute_plan(
            plan, version=v1, actor="author",
            handlers=build_default_handlers(project_manager=pm),
        )
        assert results[0].status == "OK"
        assert results[0].result["status"] == "OK"
        assert results[0].result["event_id"]
    finally:
        _restore_timeline(prev)
