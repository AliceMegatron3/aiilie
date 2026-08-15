"""
services/timeline_service.py — 补丁1：非线性叙事时间轴系统服务
================================================================
TimelineService   ：时间线/事件 CRUD（持久化挂载在 AuthorProject.timelines JSON 列）
TimelineConflictChecker：冲突检测（人物状态矛盾、时间逻辑矛盾、重复事件、轨道溢出）
"""
from __future__ import annotations

import logging
from typing import Any

from core.config_manager import config_manager
from models.timeline import Timeline, TimelineConflict, TimelineConflictReport, TimelineEvent

logger = logging.getLogger(__name__)


def _feature_enabled() -> bool:
    return config_manager.get_bool("feature.timeline_enable", False)


class TimelineConflictChecker:
    """时间线冲突检测器：检测人物状态与时间逻辑矛盾。"""

    def check_timeline(self, timeline: Timeline) -> TimelineConflictReport:
        """对单条时间线执行全量冲突检测，输出冲突报告。"""
        conflicts: list[TimelineConflict] = []
        events = sorted(
            timeline.events, key=lambda e: (e.track_index, e.position_x, e.created_at)
        )

        # ── 1. 人物状态矛盾：同人物在不同事件中出现互斥状态（如既存活又已死） ──
        seen_states: dict[str, tuple[str, TimelineEvent]] = {}
        for ev in events:
            for char, state in ev.character_states.items():
                norm_state = state.strip()
                if not norm_state:
                    continue
                prev = seen_states.get(char)
                if prev is None:
                    seen_states[char] = (norm_state, ev)
                    continue
                prev_state, prev_ev = prev
                if prev_state != norm_state and _states_conflict(prev_state, norm_state):
                    conflicts.append(
                        TimelineConflict(
                            severity="ERROR",
                            conflict_type="CHARACTER_STATE",
                            message=(
                                f"人物「{char}」状态矛盾："
                                f"事件「{prev_ev.title}」中为「{prev_state}」，"
                                f"事件「{ev.title}」中为「{norm_state}」"
                            ),
                            event_id=ev.event_id,
                            related_event_id=prev_ev.event_id,
                            character=char,
                        )
                    )
                seen_states[char] = (norm_state, ev)

        # ── 2. 时间逻辑矛盾：带 story_time 的事件按叙事时间点排序后倒挂 ──
        timed_events = [e for e in events if e.story_time]
        if len(timed_events) >= 2:
            for i in range(len(timed_events) - 1):
                a, b = timed_events[i], timed_events[i + 1]
                # 仅能对可比对的叙事时间（同前缀）判断顺序
                order = _compare_story_time(a.story_time, b.story_time)
                if order is not None and order > 0 and a.position_x >= b.position_x:
                    conflicts.append(
                        TimelineConflict(
                            severity="WARNING",
                            conflict_type="TEMPORAL_ORDER",
                            message=(
                                f"叙事时间倒挂：事件「{a.title}」（{a.story_time}）"
                                f"晚于「{b.title}」（{b.story_time}），"
                                f"但在时间轴上的位置更靠前"
                            ),
                            event_id=a.event_id,
                            related_event_id=b.event_id,
                        )
                    )

        # ── 3. 重复事件：同轨道、相近位置、同名 ──
        for i in range(len(events)):
            for j in range(i + 1, len(events)):
                a, b = events[i], events[j]
                if (
                    a.track_index == b.track_index
                    and a.title == b.title
                    and abs(a.position_x - b.position_x) < 0.5
                ):
                    conflicts.append(
                        TimelineConflict(
                            severity="WARNING",
                            conflict_type="DUPLICATE_EVENT",
                            message=f"疑似重复事件：「{a.title}」在同一轨道同一位置出现两次",
                            event_id=b.event_id,
                            related_event_id=a.event_id,
                        )
                    )

        # ── 4. 轨道溢出：track_index 超过时间线轨道数 ──
        track_count = max(1, len(timeline.tracks))
        for ev in events:
            if ev.track_index >= track_count:
                conflicts.append(
                    TimelineConflict(
                        severity="WARNING",
                        conflict_type="TRACK_OVERFLOW",
                        message=(
                            f"事件「{ev.title}」轨道索引 {ev.track_index} "
                            f"超出时间线轨道数 {track_count}"
                        ),
                        event_id=ev.event_id,
                    )
                )

        return TimelineConflictReport(
            timeline_id=timeline.timeline_id,
            conflict_count=len(conflicts),
            conflicts=conflicts,
            checked_event_count=len(events),
        )


def _states_conflict(s1: str, s2: str) -> bool:
    """判定两个人物状态是否互斥（简单规则：生命状态类互斥）。"""
    alive_words = {"存活", "活着", "健康", "在生", "alive"}
    dead_words = {"已死", "死亡", "死亡状态", "阵亡", "dead", "牺牲"}
    s1_l, s2_l = s1.lower(), s2.lower()
    if (s1_l in alive_words or any(w in s1_l for w in alive_words)) and (
        s2_l in dead_words or any(w in s2_l for w in dead_words)
    ):
        return True
    if (s2_l in alive_words or any(w in s2_l for w in alive_words)) and (
        s1_l in dead_words or any(w in s1_l for w in dead_words)
    ):
        return True
    return False


def _compare_story_time(a: str | None, b: str | None) -> int | None:
    """比较叙事时间点。返回 -1/0/1；不可比时返回 None。"""
    if not a or not b:
        return None
    a_num = _extract_time_number(a)
    b_num = _extract_time_number(b)
    if a_num is None or b_num is None:
        return None
    # 若前缀（时间线纪元）不同则不可比
    a_prefix = a[: a.index(str(a_num))] if str(a_num) in a else ""
    b_prefix = b[: b.index(str(b_num))] if str(b_num) in b else ""
    if a_prefix.strip() and b_prefix.strip() and a_prefix != b_prefix:
        return None
    return (a_num > b_num) - (a_num < b_num)


def _extract_time_number(value: str) -> int | None:
    """从叙事时间字符串提取可比较的数值（如 '第3章'→3、'公元前300年'→-300）。"""
    import re

    m = re.search(r"-?\d+", value)
    if not m:
        return None
    num = int(m.group())
    if "公元前" in value or "bc" in value.lower():
        num = -num
    return num


class TimelineService:
    """时间线 CRUD 服务：读写挂载在 AuthorProject.timelines 上。"""

    def __init__(self, project_manager) -> None:
        self._pm = project_manager
        self._conflict_checker = TimelineConflictChecker()

    async def _load_project(self, project_id: str):
        from models.project import AuthorProject

        project = await self._pm.get_project(project_id)
        if project is None:
            raise ValueError(f"项目不存在: {project_id}")
        return project

    async def _save_project(self, project) -> None:
        await self._pm.update_project(project)

    @staticmethod
    def _find_timeline(project, timeline_id: str) -> Timeline | None:
        return next((t for t in project.timelines if t.timeline_id == timeline_id), None)

    @staticmethod
    def _find_event(timeline: Timeline, event_id: str) -> TimelineEvent | None:
        return next((e for e in timeline.events if e.event_id == event_id), None)

    # ── 时间线 CRUD ──────────────────────────────────────────────

    async def list_timelines(self, project_id: str) -> list[Timeline]:
        project = await self._load_project(project_id)
        return project.timelines

    async def create_timeline(self, project_id: str, timeline: Timeline) -> Timeline:
        project = await self._load_project(project_id)
        max_tracks = config_manager.get_int("timeline.max_tracks", 6)
        if len(project.timelines) >= max_tracks:
            raise ValueError(f"时间线数量已达上限（{max_tracks}）")
        if self._find_timeline(project, timeline.timeline_id) is not None:
            raise ValueError(f"时间线已存在: {timeline.timeline_id}")
        project.timelines.append(timeline)
        await self._save_project(project)
        logger.info("创建时间线: %s (项目 %s)", timeline.name, project_id)
        return timeline

    async def update_timeline(
        self, project_id: str, timeline_id: str, patch: Timeline
    ) -> Timeline:
        project = await self._load_project(project_id)
        idx = next(
            (i for i, t in enumerate(project.timelines) if t.timeline_id == timeline_id),
            None,
        )
        if idx is None:
            raise ValueError(f"时间线不存在: {timeline_id}")
        patch.timeline_id = timeline_id  # 防篡改 ID
        project.timelines[idx] = patch
        await self._save_project(project)
        logger.info("更新时间线: %s", timeline_id)
        return patch

    async def delete_timeline(self, project_id: str, timeline_id: str) -> None:
        project = await self._load_project(project_id)
        project.timelines = [t for t in project.timelines if t.timeline_id != timeline_id]
        await self._save_project(project)
        logger.info("删除时间线: %s", timeline_id)

    async def get_timeline(self, project_id: str, timeline_id: str) -> Timeline:
        project = await self._load_project(project_id)
        timeline = self._find_timeline(project, timeline_id)
        if timeline is None:
            raise ValueError(f"时间线不存在: {timeline_id}")
        return timeline

    # ── 事件 CRUD ────────────────────────────────────────────────

    async def add_event(
        self, project_id: str, timeline_id: str, event: TimelineEvent
    ) -> TimelineEvent:
        project = await self._load_project(project_id)
        timeline = self._find_timeline(project, timeline_id)
        if timeline is None:
            raise ValueError(f"时间线不存在: {timeline_id}")
        max_events = config_manager.get_int("timeline.max_events_per_timeline", 200)
        if len(timeline.events) >= max_events:
            raise ValueError(f"时间线事件数已达上限（{max_events}）")
        timeline.events.append(event)
        await self._save_project(project)
        logger.info("时间线 %s 新增事件: %s", timeline_id, event.title)
        return event

    async def update_event(
        self, project_id: str, timeline_id: str, event_id: str, patch: TimelineEvent
    ) -> TimelineEvent:
        project = await self._load_project(project_id)
        timeline = self._find_timeline(project, timeline_id)
        if timeline is None:
            raise ValueError(f"时间线不存在: {timeline_id}")
        idx = next(
            (i for i, e in enumerate(timeline.events) if e.event_id == event_id), None
        )
        if idx is None:
            raise ValueError(f"事件不存在: {event_id}")
        patch.event_id = event_id  # 防篡改 ID
        timeline.events[idx] = patch
        await self._save_project(project)
        logger.info("时间线 %s 更新事件: %s", timeline_id, event_id)
        return patch

    async def delete_event(self, project_id: str, timeline_id: str, event_id: str) -> None:
        project = await self._load_project(project_id)
        timeline = self._find_timeline(project, timeline_id)
        if timeline is None:
            raise ValueError(f"时间线不存在: {timeline_id}")
        timeline.events = [e for e in timeline.events if e.event_id != event_id]
        await self._save_project(project)
        logger.info("时间线 %s 删除事件: %s", timeline_id, event_id)

    # ── 冲突检测 ────────────────────────────────────────────────

    async def check_conflicts(self, project_id: str, timeline_id: str) -> TimelineConflictReport:
        timeline = await self.get_timeline(project_id, timeline_id)
        return self._conflict_checker.check_timeline(timeline)

    async def check_all_conflicts(self, project_id: str) -> list[TimelineConflictReport]:
        project = await self._load_project(project_id)
        return [self._conflict_checker.check_timeline(t) for t in project.timelines]
