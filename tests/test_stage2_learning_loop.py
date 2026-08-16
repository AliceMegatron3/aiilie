"""阶段2学习环接线回归测试(讨论稿20260816第三章修复顺序第2步)。

覆盖三组改动:
A. 反思快照→抽取器→应用器接线(此前 RuleExtractor/SkillExtractor
   实例化后从未被调用,快照落盘即死胡同)
B. novellearn_ 自学习任务不再重复拍全量快照(bootstrap 分派逻辑,
   由代码评审确认,不做单测)
C. 评分持久化(ScoringEngine JSONL 落盘,路径可注入)
"""
from __future__ import annotations

import json

import pytest

from core.database import DatabaseManager
from models.reflection import OptimizationRule, UniversalSkill
from models.task import ReflectionTask


class _FakeCollector:
    def __init__(self):
        self.called = 0

    async def collect_snapshot(self):
        self.called += 1
        return {"tasks": [], "cards": [], "projects": []}


class _FakeRuleExtractor:
    def __init__(self, rules=None, raise_exc: Exception | None = None):
        self.rules = rules or []
        self.raise_exc = raise_exc
        self.snapshots = []

    def extract_rules(self, snapshot):
        if self.raise_exc:
            raise self.raise_exc
        self.snapshots.append(snapshot)
        return self.rules


class _FakeSkillExtractor:
    def __init__(self, skills=None, raise_exc: Exception | None = None):
        self.skills = skills or []
        self.raise_exc = raise_exc

    async def extract_skills(self, snapshot):
        if self.raise_exc:
            raise self.raise_exc
        return self.skills


class _FakeApplier:
    def __init__(self):
        self.calls = []

    async def apply_discoveries(self, rules, skills):
        self.calls.append({"rules": list(rules), "skills": list(skills)})


async def _make_trigger(tmp_path, monkeypatch, rule_ext=None, skill_ext=None, applier=None,
                        session_id: str = "sess_stage2"):
    import services.reflection_trigger as rt

    monkeypatch.setattr(rt, "get_app_data_dir", lambda: tmp_path)
    db = DatabaseManager(db_path=tmp_path / "stage2.db")
    await db.initialize()
    # 手工建表并预插会话行(生产中由 trigger() 创建),避免 initialize() 启动 cron 调度器
    await db.conn.execute("""
        CREATE TABLE IF NOT EXISTS reflection_sessions (
            session_id TEXT PRIMARY KEY,
            trigger_type TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            status TEXT NOT NULL,
            report_path TEXT
        )
    """)
    from datetime import datetime, timezone

    await db.conn.execute(
        "INSERT INTO reflection_sessions (session_id, trigger_type, start_time, status, report_path)"
        " VALUES (?, ?, ?, ?, ?)",
        (session_id, "MANUAL", datetime.now(timezone.utc).isoformat(), "PENDING",
         str(tmp_path / f"{session_id}_raw.json")),
    )
    await db.conn.commit()
    collector = _FakeCollector()
    trigger = rt.ReflectionTrigger(db, task_manager=None, collector=collector)
    if rule_ext is not None or skill_ext is not None or applier is not None:
        trigger.attach_miners(rule_ext, skill_ext, applier)
    return trigger, db


@pytest.mark.asyncio
async def test_reflection_mines_snapshot_after_collection(tmp_path, monkeypatch):
    rule = OptimizationRule(
        scope="TASK_SPLIT",
        condition={"estimated_length": {">": 3000}},
        action={"force_split_chunks": True},
        confidence=0.8,
    )
    skill = UniversalSkill(type="TEMPLATE", name="章节结构模板", content={"beats": 3})
    rule_ext = _FakeRuleExtractor(rules=[rule])
    skill_ext = _FakeSkillExtractor(skills=[skill])
    applier = _FakeApplier()
    trigger, db = await _make_trigger(
        tmp_path, monkeypatch, rule_ext=rule_ext, skill_ext=skill_ext, applier=applier,
        session_id="sess_stage2a",
    )
    try:
        task = ReflectionTask(task_id="sess_stage2a", session_id="sess_stage2a", priority=7)
        await trigger.process_task(task)

        # 会话完结
        cursor = await db.conn.execute(
            "SELECT status FROM reflection_sessions WHERE session_id = ?", ("sess_stage2a",)
        )
        assert (await cursor.fetchone())[0] == "COMPLETED"
        # 快照被采集且被矿工消费
        assert trigger.collector.called == 1
        assert rule_ext.snapshots and "tasks" in rule_ext.snapshots[0]
        # 挖掘成果下发到应用器(此前 apply_discoveries 零调用方)
        assert len(applier.calls) == 1
        assert applier.calls[0]["rules"] == [rule]
        assert applier.calls[0]["skills"] == [skill]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_miner_failure_does_not_break_session(tmp_path, monkeypatch):
    applier = _FakeApplier()
    trigger, db = await _make_trigger(
        tmp_path, monkeypatch,
        rule_ext=_FakeRuleExtractor(raise_exc=ValueError("规则抽取炸了")),
        skill_ext=_FakeSkillExtractor(raise_exc=RuntimeError("技能抽取炸了")),
        applier=applier,
        session_id="sess_stage2b",
    )
    try:
        task = ReflectionTask(task_id="sess_stage2b", session_id="sess_stage2b", priority=7)
        await trigger.process_task(task)
        cursor = await db.conn.execute(
            "SELECT status FROM reflection_sessions WHERE session_id = ?", ("sess_stage2b",)
        )
        # 挖掘失败不阻断反思会话主流程
        assert (await cursor.fetchone())[0] == "COMPLETED"
        assert applier.calls == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_scoring_persists_jsonl_history(tmp_path):
    from services.scoring_engine import ScoringEngine

    engine = ScoringEngine(history_path=tmp_path / "scoring" / "history.jsonl")
    r1 = await engine.score_generation(
        project_id="proj-1",
        command_text="写一场战斗：主角持剑杀出重围",
        generated_content="他挥了挥手中的剑，周围的人都退开了，然后战斗结束了，一切归于平静，没有任何波澜。" * 3,
        used_cards=[{"card_id": "c1"}],
    )
    assert 0 <= r1["score"] <= 100
    r2 = await engine.score_generation(
        project_id="proj-1", command_text="闲笔", generated_content="短句。", used_cards=[]
    )

    path = tmp_path / "scoring" / "history.jsonl"
    assert path.exists()
    lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2
    assert lines[0]["project_id"] == "proj-1"
    assert lines[0]["heuristic"] is True
    assert "score" in lines[0] and "improvements" in lines[0]
    assert lines[0]["used_cards_count"] == 1
    assert lines[1]["command_excerpt"] == "闲笔"
