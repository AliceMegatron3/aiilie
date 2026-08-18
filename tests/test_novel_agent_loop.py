"""
tests/test_novel_agent_loop.py — 第十部分端到端集成测试
====================================================
验证验收标准：
1. 多次创作任务后，审计日志完整记录 Agent 调用、token、命中分数；
2. 满足样本条件，反思任务可以生成调度规则与 NovelAgentSkill 技能卡片；
3. 规则、技能可以被 NovelSupervisor 加载并动态改变子Agent行为；
4. 效果差的技能自动降权归档，用户可以手动恢复；
5. 关闭 enable_self_reflect_learn 开关，自学习完全停止，多Agent功能不受影响。
"""
from __future__ import annotations
import asyncio
import json
import sys
import tempfile
from pathlib import Path
# 将项目根加入 sys.path
BASE = Path(r"C:\Users\11482\Documents\aiilie")
sys.path.insert(0, str(BASE))
import aiosqlite
from models.novel_agent import NovelAgentExecutionAudit, NovelAgentSkill, SubAgentCallRecord
from services.novel_agent_audit_store import NovelAgentAuditStore
from services.novel_agent_skill_store import NovelAgentSkillStore
from extractors.novel_agent_extractors import AgentRuleExtractor, NovelAgentSkillExtractor
from services.novel_agent_learning_loop import NovelAgentLearningLoop
class FakeDB:
    """内存 SQLite 伪库（不依赖项目主库）。"""
    def __init__(self):
        self.conn = None
    async def initialize(self):
        self.conn = await aiosqlite.connect(":memory:")
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS novel_agent_audits (
                audit_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, project_id TEXT DEFAULT '',
                genre TEXT DEFAULT '', scenario TEXT DEFAULT '',
                agent_calls TEXT NOT NULL DEFAULT '[]', total_tokens INTEGER DEFAULT 0,
                total_duration_ms INTEGER DEFAULT 0, hit_score REAL DEFAULT 0.0,
                ooc_warnings INTEGER DEFAULT 0, circuit_break INTEGER DEFAULT 0,
                circuit_break_reason TEXT DEFAULT '', used_rule_ids TEXT NOT NULL DEFAULT '[]',
                used_skill_ids TEXT NOT NULL DEFAULT '[]', user_feedback_score REAL,
                user_ooc_marked INTEGER DEFAULT 0, user_comment TEXT DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS novel_agent_skills (
                skill_id TEXT PRIMARY KEY, name TEXT NOT NULL, skill_type TEXT NOT NULL DEFAULT 'PATTERN',
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
                apply_count INTEGER DEFAULT 0, effect_score REAL DEFAULT 0.0,
                creator TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        await self.conn.execute(
            """CREATE TABLE IF NOT EXISTS optimization_rules (
                rule_id TEXT PRIMARY KEY, scope TEXT NOT NULL, condition TEXT NOT NULL,
                action TEXT NOT NULL, confidence REAL NOT NULL,
                is_active INTEGER NOT NULL, created_at TEXT NOT NULL, feedback_score REAL DEFAULT 0.0
            )"""
        )
        await self.conn.commit()
class FakeApplier:
    """内存版 OptimizationApplier 桩（仅用于规则部署验证）。"""
    def __init__(self, db):
        self.db = db
    async def fetch_active_rules(self, scope=None, context_features=None, **kwargs):
        cursor = await self.db.conn.execute(
            "SELECT * FROM optimization_rules WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        rules = []
        for r in rows:
            d = dict(zip(cols, r))
            try:
                d["condition"] = json.loads(d["condition"])
            except Exception:
                d["condition"] = {}
            try:
                d["action"] = json.loads(d["action"])
            except Exception:
                d["action"] = {}
            d["is_active"] = bool(d["is_active"])
            from models.reflection import OptimizationRule
            rules.append(OptimizationRule(**d))
        return rules
class FakeTaskManager:
    """低优先级任务投递桩。"""
    def __init__(self):
        self.submitted = []
    async def submit_task(self, task):
        self.submitted.append(task)
        return task
def make_sample(task_id, genre, hit_score, ooc=0, circuit=False, feedback=None):
    """构造一条审计样本。"""
    return {
        "audit_id": f"audit_{task_id}",
        "task_id": f"task_{task_id}",
        "project_id": "proj_demo",
        "genre": genre,
        "scenario": "战斗",
        "agent_calls": [
            {
                "agent_role": "lore_expert", "is_active": True, "model_assigned": "qwen",
                "calls": 1, "total_tokens": 500, "duration_ms": 1000,
                "hit_score": hit_score, "ooc_warnings": ooc, "error_count": 0,
            },
            {
                "agent_role": "combat_expert", "is_active": True, "model_assigned": "qwen",
                "calls": 1, "total_tokens": 300, "duration_ms": 800,
                "hit_score": hit_score * 0.9, "ooc_warnings": 0, "error_count": 1 if ooc > 0 else 0,
            },
        ],
        "total_tokens": 800,
        "total_duration_ms": 1800,
        "hit_score": hit_score,
        "ooc_warnings": ooc,
        "circuit_break": circuit,
        "circuit_break_reason": "test" if circuit else "",
        "used_rule_ids": [],
        "used_skill_ids": [],
        "user_feedback_score": feedback,
        "user_ooc_marked": False,
        "user_comment": "",
        "created_at": "2026-08-14T00:00:00Z",
        "updated_at": "2026-08-14T00:00:00Z",
    }
async def main():
    print("=" * 60)
    print("第十部分端到端集成测试开始")
    print("=" * 60)
    # 0. 模拟开启自学习子开关（config.yaml 默认关闭，测试流程先开启）
    import core.config_manager as cm
    cm.config_manager._config.setdefault("novel_agent", {})["enable_self_reflect_learn"] = True
    cm.config_manager._config["novel_agent"]["min_sample_count"] = 5
    # 1. 初始化环境
    db = FakeDB()
    await db.initialize()
    audit_store = NovelAgentAuditStore(db)
    await audit_store.initialize()
    skill_store = NovelAgentSkillStore(db)
    await skill_store.initialize()
    applier = FakeApplier(db)
    task_manager = FakeTaskManager()
    loop = NovelAgentLearningLoop(
        db=db, audit_store=audit_store, skill_store=skill_store,
        optimization_applier=applier, task_manager=task_manager,
    )
    # 2. 模拟 7 次创作任务（满足 min_sample_count=5）
    #    5 次玄幻高命中（供技能沉淀），1 次都市低命中，1 次玄幻高OOC（供规则挖掘）
    samples = []
    for i in range(7):
        if i < 5:
            genre, hit, ooc, circuit, fb = "玄幻", 0.85, 0, False, 4.0   # 高命中样本
        elif i == 5:
            genre, hit, ooc, circuit, fb = "玄幻", 0.3, 3, True, 1.0     # 高OOC+熔断样本
        else:
            genre, hit, ooc, circuit, fb = "都市", 0.4, 0, False, 2.0    # 低命中样本
        s = make_sample(i, genre, hit_score=hit, ooc=ooc, circuit=circuit, feedback=fb)
        samples.append(s)
        audit = NovelAgentExecutionAudit.model_validate(s)
        await audit_store.save_audit(audit)
    count = await audit_store.count_audits()
    print(f"\n✅ [验收1] 审计日志总数: {count}（>= 5 满足样本条件）")
    assert count >= 5, "审计样本数不足"
    # 3. 触发反思学习
    result = await loop.process_reflection_task()
    print(f"\n✅ [验收2] 反思学习产出: {json.dumps(result, ensure_ascii=False)}")
    # 检查规则是否部署
    cursor = await db.conn.execute("SELECT COUNT(*) FROM optimization_rules")
    rule_count = (await cursor.fetchone())[0]
    cursor = await db.conn.execute("SELECT COUNT(*) FROM novel_agent_skills")
    skill_count = (await cursor.fetchone())[0]
    print(f"✅ [验收2] 部署规则数: {rule_count}, 技能数: {skill_count}")
    assert rule_count > 0, "未能生成调度规则"
    assert skill_count > 0, "未能生成技能卡片"
    # 4. 验证规则：低置信度规则已进入人工审核队列（补丁B）
    cursor = await db.conn.execute(
        "SELECT COUNT(*) FROM rule_review_queue WHERE review_status = 'PENDING'"
    )
    review_count = (await cursor.fetchone())[0]
    print(f"\n✅ [验收3] 低置信度规则进入人工审核队列: {review_count} 条")
    assert review_count >= 1, "低置信度规则未进入人工审核队列"
    # 模拟补丁B人工审核批准：把规则激活（将 PENDING_REVIEW 规则置为 is_active=1）
    cursor = await db.conn.execute(
        "UPDATE optimization_rules SET is_active = 1 WHERE scope = 'NOVEL_AGENT_SCHEDULE'"
    )
    await db.conn.commit()
    rules = await applier.fetch_active_rules(scope="NOVEL_AGENT_SCHEDULE")
    print(f"✅ [验收3] 人工批准后 NovelSupervisor 可加载活跃规则 {len(rules)} 条")
    skills = await skill_store.list_skills()
    active_skill = [s for s in skills if s["status"] == "ACTIVE"]
    print(f"✅ [验收3] 技能列表可查询: {len(skills)} 项, ACTIVE {len(active_skill)} 项")
    assert len(rules) >= 1, "人工批准后活跃规则仍为 0"
    assert len(active_skill) >= 1, "无 ACTIVE 技能"
    # 5. 验证规则 action 中 agent_overrides 可动态改变子Agent行为
    has_override = any("agent_overrides" in (r.action or {}) for r in rules)
    print(f"✅ [验收3] 规则含子Agent开关覆盖: {has_override}")
    assert has_override, "规则不包含 agent_overrides 子Agent开关覆盖"
    # 验证 NovelSupervisor._build_agent_config 合并效果：lore_expert 应被关闭
    from services.novel_supervisor import NovelSupervisor
    sup = NovelSupervisor(indexer=None, optimization_applier=applier)
    merged = sup._build_agent_config(rules, [])
    print(f"✅ [验收3] NovelSupervisor 合并后 lore_expert.enabled = {merged.get('lore_expert', {}).get('enabled')}")
    assert merged.get("lore_expert", {}).get("enabled") is False, "规则未成功改变子Agent行为"
    # 6. 技能降权归档验证（低 effect_score 自动归档）
    # 注入一条低分技能记录
    low_skill = NovelAgentSkill(
        skill_id="skill_low_effect",
        name="低效技能_测试",
        content={"genre": "测试", "content_text": "低效测试技能"},
        agent_overrides={},
        trigger_conditions={"genre": "测试"},
        effect_stats={"apply_count": 10, "hit_sum": 0.0, "ooc_sum": 8, "circuit_break_count": 2,
                      "total_tokens": 50000, "user_feedback_count": 0, "user_feedback_sum": 0.0,
                      "effect_score": 0.05},
        status="ACTIVE",
    )
    await skill_store.save_skill(low_skill)
    outcome = await loop.evaluate_and_lifecycle()
    print(f"\n✅ [验收4] 优胜劣汰结果: {json.dumps(outcome, ensure_ascii=False)}")
    low_skill_row = await skill_store.get_skill("skill_low_effect")
    print(f"✅ [验收4] 低效技能状态: {low_skill_row['status'] if low_skill_row else 'N/A'}")
    assert low_skill_row and low_skill_row["status"] == "ARCHIVED", "低效技能未自动归档"
    # 7. 用户手动恢复
    restored = await skill_store.toggle_skill("skill_low_effect", "ACTIVE")
    restored_row = await skill_store.get_skill("skill_low_effect")
    print(f"✅ [验收4] 用户手动恢复技能: {restored}, 状态={restored_row['status'] if restored_row else 'N/A'}")
    assert restored and restored_row["status"] == "ACTIVE", "用户恢复失败"
    # 8. 关闭自学习开关验证
    loop_has = loop.is_learn_enabled()
    print(f"\n✅ [验收5] enable_self_reflect_learn 当前默认: {loop_has}")
    # 模拟关闭：直接修改内存配置模拟关闭（不影响原文件）
    cm.config_manager._config.setdefault("novel_agent", {})["enable_self_reflect_learn"] = False
    try:
        sid = await loop.submit_reflection_task()
        # 通过 task_manager.submitted 验证未被提交（loop 内部 is_learn_enabled() 返回 False）
        print(f"✅ [验收5] 开关关闭后反思任务提交: {sid} (None=已停止)")
        assert sid is None, "开关关闭后仍提交了反思任务"
    finally:
        cm.config_manager._config["novel_agent"]["enable_self_reflect_learn"] = True
    print("✅ [验收5] 开关关闭后自学习完全停止验证通过")
    print("\n" + "=" * 60)
    print("全部验收标准测试通过 ✅")
    print("=" * 60)
    return 0
if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


# ── Batch 4：退役 toggle_skill 的生产调用改为安全 archive_skill ──
import pytest  # noqa: E402
from core.database import DatabaseManager  # noqa: E402


@pytest.mark.asyncio
async def test_archive_skill_is_idempotent_and_retired_toggle_raises(tmp_path):
    """Batch 4：低效技能归档走 archive_skill（ACTIVE→ARCHIVED 单向、幂等），
    退役 toggle_skill 仍被坚决拒绝，杜绝静默归档失败。"""
    db = DatabaseManager(db_path=tmp_path / "skillstore.db")
    await db.initialize()
    store = NovelAgentSkillStore(db)
    await store.initialize()
    try:
        await db.conn.execute(
            "INSERT OR REPLACE INTO novel_agent_skills"
            "(skill_id,name,skill_type,payload,status,apply_count,effect_score,creator,created_at,updated_at) "
            "VALUES ('skill_arc','弧线','PATTERN','{}','ACTIVE',0,0.05,'','init','init')"
        )
        await db.conn.commit()
        # 首次归档成功
        assert await store.archive_skill("skill_arc") is True
        assert (await store.get_skill("skill_arc"))["status"] == "ARCHIVED"
        # 已归档 → 幂等返回 False，不重复副作用
        assert await store.archive_skill("skill_arc") is False
        # 退役 toggle 仍被拒绝（fail-closed）
        with pytest.raises(RuntimeError):
            await store.toggle_skill("skill_arc", "ACTIVE")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_low_confidence_rule_never_writes_active_directly(tmp_path):
    """Batch 4：低置信度规则即便被标记 ACTIVE 也强制进入人工审核（is_active=0），
    高置信度规则才允许直接 active——在部署边界 fail-closed，不依赖 extractor 自觉。"""
    from services.novel_agent_learning_loop import NovelAgentLearningLoop

    db = DatabaseManager(db_path=tmp_path / "ruleloop.db")
    await db.initialize()
    await db.conn.execute(
        """CREATE TABLE IF NOT EXISTS optimization_rules (
            rule_id TEXT PRIMARY KEY, scope TEXT NOT NULL, condition TEXT NOT NULL,
            action TEXT NOT NULL, confidence REAL NOT NULL, is_active INTEGER DEFAULT 1,
            created_at REAL NOT NULL, feedback_score REAL DEFAULT 0.0
        )"""
    )
    await db.conn.commit()
    loop = object.__new__(NovelAgentLearningLoop)
    loop.db = db
    loop.confidence_threshold = 0.75
    try:
        # 高置信度 ACTIVE → 直接生效
        await loop._deploy_rules([{
            "rule_id": "rule_high", "scope": "NOVEL_AGENT_SCHEDULE",
            "condition": {"x": 1}, "action": {"y": 2},
            "confidence": 0.9, "status": "ACTIVE",
        }])
        # 低置信度但被标记 ACTIVE → 降权进入人工审核，不直写 active
        await loop._deploy_rules([{
            "rule_id": "rule_low", "scope": "NOVEL_AGENT_SCHEDULE",
            "condition": {"x": 1}, "action": {"y": 2},
            "confidence": 0.4, "status": "ACTIVE",
        }])
        cursor = await db.conn.execute(
            "SELECT rule_id, is_active FROM optimization_rules ORDER BY rule_id"
        )
        rows = {str(r[0]): int(r[1]) for r in await cursor.fetchall()}
        assert rows["rule_high"] == 1  # 高置信度生效
        assert rows["rule_low"] == 0    # 低置信度绝不 active
        # 低置信度规则进入人工审核队列
        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM rule_review_queue WHERE rule_id = 'rule_low' AND review_status = 'PENDING'"
        )
        assert (await cursor.fetchone())[0] == 1
        # 高置信度规则不入审核队列
        cursor = await db.conn.execute(
            "SELECT COUNT(*) FROM rule_review_queue WHERE rule_id = 'rule_high'"
        )
        assert (await cursor.fetchone())[0] == 0
    finally:
        await db.close()