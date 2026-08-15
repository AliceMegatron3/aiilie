"""
services/optimization_applier.py — 自学习反馈执行与规则下发层
=============================================================
批次 4 的"执行引擎"。负责将 Extractor 提炼出的高置信度规则与技能落盘。
提供非侵入式的查询与挂载接口，供批次 1/2/3 在运行时动态调用，
支持热插拔的一键启停与反馈修正。
"""
from __future__ import annotations
import json
import logging
from typing import Any
from core.database import DatabaseManager
from models.reflection import OptimizationRule, UniversalSkill
logger = logging.getLogger(__name__)
class OptimizationApplier:
    """
    负责固化、管理、分发动态优化规则与高频创作模板。
    """
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
    async def initialize(self) -> None:
        """初始化批次4独有的下发配置表，严格隔离不污染存量业务数据"""
        try:
            await self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS optimization_rules (
                    rule_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    is_active INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    feedback_score REAL DEFAULT 0.0
                )
            """)
            await self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS universal_skills (
                    skill_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_cards TEXT NOT NULL,
                    applicability TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            await self.db.conn.commit()
            logger.info("系统优化规则表与通用技能模板表初始化完成")
        except Exception as e:
            logger.error("初始化优化规则表失败: %s", e)
            raise
    async def apply_discoveries(self, rules: list[OptimizationRule], skills: list[UniversalSkill]) -> None:
        """
        批量下发与持久化挖掘成果。
        仅注入新产生的唯一规则，防范死循环与冗余膨胀。
        """
        rule_params = []
        for r in rules:
            rule_params.append((
                r.rule_id,
                r.scope,
                json.dumps(r.condition, ensure_ascii=False),
                json.dumps(r.action, ensure_ascii=False),
                r.confidence,
                1 if r.is_active else 0,
                r.created_at
            ))
        skill_params = []
        for s in skills:
            skill_params.append((
                s.skill_id,
                s.type,
                s.name,
                json.dumps(s.content, ensure_ascii=False),
                json.dumps(s.source_cards, ensure_ascii=False),
                s.applicability,
                s.created_at
            ))
        try:
            if rule_params:
                # 采用 INSERT OR IGNORE 防止相同的挖掘产物重复写入
                await self.db.conn.executemany("""
                    INSERT OR IGNORE INTO optimization_rules
                    (rule_id, scope, condition, action, confidence, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, rule_params)
            if skill_params:
                await self.db.conn.executemany("""
                    INSERT OR IGNORE INTO universal_skills
                    (skill_id, type, name, content, source_cards, applicability, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, skill_params)
            await self.db.conn.commit()
            logger.info("[Applier] 成功部署 %d 条新规则，%d 项新技能！", len(rule_params), len(skill_params))
        except Exception as e:
            logger.error("下发优化发现成果失败: %s", e)
            raise
    # ==========================================
    # 动态外挂查询层 (供底层主动拉取拦截)
    # ==========================================
    async def fetch_active_rules(
        self,
        scope: str | None = None,
        context_features: dict[str, Any] | None = None,
        **kwargs: Any
    ) -> list[OptimizationRule]:
        """
        [外置扩展位]
        供 Batch 1 的调度器或 Batch 3 的拆分器在运行时查询：
        是否有针对当前命令状态高置信度的覆写动作。
        参数说明:
            scope: 规则作用域过滤条件（如 "MODEL_DISPATCH"、"TASK_SPLIT" 等），
                   为 None 时查询所有激活规则。
            context_features: 上下文特征字典，用于规则条件匹配。
                   包含请求的关键特征（如 command_type、estimated_length 等）。
        """
        # 兼容旧调用方式：如果通过位置参数或 kwargs 传入额外参数，忽略
        if scope:
            cursor = await self.db.conn.execute(
                "SELECT * FROM optimization_rules WHERE scope = ? AND is_active = 1 AND confidence >= 0.6",
                (scope,)
            )
        else:
            cursor = await self.db.conn.execute(
                "SELECT * FROM optimization_rules WHERE is_active = 1 AND confidence >= 0.6"
            )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        matched_rules = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            try:
                condition = json.loads(row_dict["condition"]) if row_dict.get("condition") else {}
            except (json.JSONDecodeError, TypeError):
                condition = {}
            # 执行极简前置状态匹配 (如 {"command_type": "xxx", "estimated_length": {">": 2000}})
            # 若全部匹配则作为外置拦截动作放行
            is_match = True
            if context_features is not None:
                for k, v in condition.items():
                    target_val = context_features.get(k)
                    if isinstance(v, dict):
                        if ">" in v and not (target_val is not None and target_val > v[">"]):
                            is_match = False; break
                        if "<" in v and not (target_val is not None and target_val < v["<"]):
                            is_match = False; break
                    else:
                        if target_val != v:
                            is_match = False; break
            if is_match:
                row_dict["condition"] = condition
                try:
                    row_dict["action"] = json.loads(row_dict["action"]) if row_dict.get("action") else {}
                except (json.JSONDecodeError, TypeError):
                    row_dict["action"] = {}
                row_dict["is_active"] = bool(row_dict["is_active"])
                matched_rules.append(OptimizationRule(**row_dict))
        return matched_rules
    async def fetch_recommended_skills(
        self,
        genre: str | None = None
    ) -> list[UniversalSkill]:
        """
        [外置扩展位]
        供 Batch 3 项目引擎查询，在生成内容时获取辅助技能模板。
        """
        cursor = await self.db.conn.execute("SELECT * FROM universal_skills ORDER BY created_at DESC LIMIT 50")
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            try:
                row_dict["content"] = json.loads(row_dict["content"]) if row_dict.get("content") else {}
            except (json.JSONDecodeError, TypeError):
                row_dict["content"] = {}
            try:
                row_dict["source_cards"] = json.loads(row_dict["source_cards"]) if row_dict.get("source_cards") else []
            except (json.JSONDecodeError, TypeError):
                row_dict["source_cards"] = []
            # 简易题材命中过滤，提升推荐相关性
            if genre and genre != "unknown":
                applicability = row_dict.get("applicability", "") or ""
                if genre not in applicability and "模板" not in row_dict["name"]:
                    continue
            results.append(UniversalSkill(**row_dict))
        return results
    # ==========================================
    # 规则风控与治理操作
    # ==========================================
    async def toggle_rule_status(self, rule_id: str, is_active: bool) -> None:
        """人工一键熔断异常规则（强制回滚开关）"""
        active_int = 1 if is_active else 0
        await self.db.conn.execute(
            "UPDATE optimization_rules SET is_active = ? WHERE rule_id = ?",
            (active_int, rule_id)
        )
        await self.db.conn.commit()
        logger.info("规则 [%s] 状态已切换为: %s", rule_id, "ACTIVE" if is_active else "DISABLED")
    async def record_feedback(self, rule_id: str, is_success: bool) -> None:
        """
        策略效果反馈，形成进化闭环：
        对应用该规则后执行成功的任务，微调提升置信度；对失败的微调降权。
        """
        cursor = await self.db.conn.execute("SELECT confidence FROM optimization_rules WHERE rule_id = ?", (rule_id,))
        row = await cursor.fetchone()
        if not row:
            return
        current_conf = row[0]
        # 贝叶斯平滑简单模拟：奖罚梯度设定
        if is_success:
            new_conf = min(0.99, current_conf + 0.02)
        else:
            new_conf = max(0.1, current_conf - 0.05)
        await self.db.conn.execute(
            "UPDATE optimization_rules SET confidence = ? WHERE rule_id = ?",
            (new_conf, rule_id)
        )
        await self.db.conn.commit()
        logger.debug("规则 %s 收到反馈 [%s], 置信度变更为 %.3f", rule_id, is_success, new_conf)