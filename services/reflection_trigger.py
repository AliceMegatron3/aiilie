"""
services/reflection_trigger.py — 反思触发器与数据采集器
======================================================
管理批次4中反思任务的启动、并发拦截、断点续跑托管。
包含 DataCollector 组件，在不污染存量数据的前提下，安全读取批次1、2、3的运行时历史。
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from models.task import ReflectionTask
import asyncio
from core.database import DatabaseManager
from core.path_resolver import get_app_data_dir
from core.task_manager import TaskManager
from core.config_manager import config_manager
from models.reflection import ReflectionSession
from services.indexer import CardIndexer
from services.project_manager import ProjectManager
from croniter import croniter
logger = logging.getLogger(__name__)
class DataCollector:
    """
    系统全局数据只读快照采集器。
    严格保证不执行任何 UPDATE/INSERT/DELETE 操作，绝不污染原有业务逻辑。
    """
    def __init__(
        self,
        db: DatabaseManager,       # Batch 1
        indexer: CardIndexer,      # Batch 2
        pm: ProjectManager         # Batch 3
    ) -> None:
        self.db = db
        self.indexer = indexer
        self.pm = pm
        # 性能优化：缓存 cards 表列结构，避免每次采集重复执行 PRAGMA table_info
        self._cards_columns: list[str] | None = None
        self._cards_schema_version: int = 0
    async def collect_snapshot(self) -> dict[str, Any]:
        """统揽全局三层业务日志，清洗对齐后返回结构化数据集供 Extractors 消费。"""
        logger.info("[DataCollector] 开始扫描批次 1, 2, 3 的存量运转日志与业务数据...")
        
        tasks_data = await self._collect_batch1_tasks()
        cards_data = await self._collect_batch2_cards()
        projects_data = await self._collect_batch3_parses()
        
        snapshot = {
            "snapshot_timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "total_tasks_analyzed": len(tasks_data),
                "high_value_cards_analyzed": len(cards_data),
                "project_docs_analyzed": len(projects_data)
            },
            "datasets": {
                "tasks": tasks_data,
                "cards": cards_data,
                "project_parses": projects_data
            }
        }
        logger.info("[DataCollector] 采集完成: %s", snapshot["metrics"])
        return snapshot
    async def _collect_batch1_tasks(self) -> list[dict[str, Any]]:
        """提取底层任务执行审计：状态、重试率等"""
        try:
            # 提取近期状态不为 PENDING 的关键任务日志
            cursor = await self.db.conn.execute(
                "SELECT task_id, status, retry_count, created_at FROM tasks WHERE status != 'PENDING' ORDER BY created_at DESC LIMIT 2000"
            )
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning("采集任务表数据受阻: %s", e)
            return []
    async def _get_cards_columns_cached(self) -> list[str]:
        """读取并缓存 cards 表列结构（PRAGMA 结果），避免高频重复查询。"""
        if self._cards_columns is None:
            cursor = await self.indexer.conn.execute("PRAGMA table_info(cards)")
            rows = await cursor.fetchall()
            self._cards_columns = [row[1] for row in rows]
            logger.info(
                "[DataCollector] cards 表列结构已缓存 (%d 列)", len(self._cards_columns)
            )
        return self._cards_columns

    async def _collect_batch2_cards(self) -> list[dict[str, Any]]:
        """提取高优知识库：利用批次2底层的 weight 检索热门或异常卡片"""
        try:
            # 性能优化：列结构走缓存，不再每次 PRAGMA
            columns = await self._get_cards_columns_cached()

            if "weight" not in columns:
                # 没有 weight 列时，使用基础查询
                cursor = await self.indexer.conn.execute(
                    "SELECT card_id, card_type, summary, tags FROM cards LIMIT 1000"
                )
            else:
                # 有 weight 列时，提取 weight>=3 的高频卡片
                cursor = await self.indexer.conn.execute(
                    "SELECT card_id, card_type, summary, tags, weight FROM cards WHERE weight >= 3 LIMIT 1000"
                )
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning("采集卡片索引库受阻: %s", e)
            return []
    async def _collect_batch3_parses(self) -> list[dict[str, Any]]:
        """提取落盘的自学习文件解析结果"""
        try:
            cursor = await self.pm.db.conn.execute(
                "SELECT doc_id, project_id, ai_parse_path FROM project_docs WHERE ai_parse_path IS NOT NULL"
            )
            rows = await cursor.fetchall()
            
            results = []
            for doc_id, project_id, path_str in rows:
                p = Path(path_str)
                if p.exists():
                    try:
                        content = json.loads(p.read_text(encoding="utf-8"))
                        results.append({
                            "doc_id": doc_id,
                            "project_id": project_id,
                            "parsed_content": content
                        })
                    except Exception:
                        pass
            return results
        except Exception as e:
            logger.warning("采集项目 AI 分析数据受阻: %s", e)
            return []
class ReflectionTrigger:
    """
    触发管理器：管理反思任务的单例排队、并发限流和持久化。
    """
    def __init__(
        self,
        db: DatabaseManager,
        task_manager: TaskManager,
        collector: DataCollector
    ) -> None:
        self.db = db
        self.task_manager = task_manager
        self.collector = collector

        self.report_dir = get_app_data_dir() / "reflection_reports"
        self.report_dir.mkdir(parents=True, exist_ok=True)

        self._is_collecting = False  # 并发锁：阻止多次连续反思引发的重复 I/O 雪崩
        self._cron_task = None

        # 阶段2（学习环接线，讨论稿20260816第三章）：快照矿工注入位。
        # 此前 RuleExtractor/SkillExtractor 在 bootstrap 实例化后从未被调用，
        # 快照落盘即死胡同（universal_skills 永远为空）。
        self.rule_extractor: Any | None = None
        self.skill_extractor: Any | None = None
        self.optimization_applier: Any | None = None

    def attach_miners(
        self, rule_extractor: Any | None, skill_extractor: Any | None,
        optimization_applier: Any | None,
    ) -> None:
        """注入快照挖掘链（规则/技能抽取器 + 成果应用器），由 bootstrap 装配。"""
        self.rule_extractor = rule_extractor
        self.skill_extractor = skill_extractor
        self.optimization_applier = optimization_applier

    async def _mine_snapshot(self, snapshot: dict[str, Any], session_id: str) -> None:
        """阶段2：快照→抽取器→应用器。任何失败不阻断反思会话主流程。"""
        if self.rule_extractor is None and self.skill_extractor is None:
            return
        rules: list = []
        skills: list = []
        try:
            if self.rule_extractor is not None:
                rules = self.rule_extractor.extract_rules(snapshot) or []
        except Exception as exc:
            logger.warning("[Reflection] 规则抽取失败（不阻断）: %s", exc)
        try:
            if self.skill_extractor is not None:
                skills = await self.skill_extractor.extract_skills(snapshot) or []
        except Exception as exc:
            logger.warning("[Reflection] 技能抽取失败（不阻断）: %s", exc)
        logger.info(
            "[Reflection] 会话 %s 挖掘完成：候选规则 %d 条、候选技能 %d 项",
            session_id, len(rules), len(skills),
        )
        # P4(浅知识入池):量化技能候选 → 行为插件candidate池
        # (注册表内强制CANDIDATE,经治理门方可运行;失败不阻断)
        if skills:
            try:
                from services.behavior_plugins import register_skill_candidates

                candidates = register_skill_candidates(list(skills))
                if candidates:
                    logger.info(
                        "[Reflection] %d 项量化技能已入行为插件candidate池", len(candidates)
                    )
            except Exception as exc:
                logger.warning("[Reflection] 技能候选入池失败(不阻断): %s", exc)
        if self.optimization_applier is not None and (rules or skills):
            try:
                await self.optimization_applier.apply_discoveries(rules, skills)
            except Exception as exc:
                logger.warning("[Reflection] 挖掘成果下发失败（不阻断）: %s", exc)
        
    def start_cron_scheduler(self):
        """启动基于 config 的 Cron 表达式定时触发器"""
        cron_expr = config_manager.get("reflection.schedule_cron", None)
        if cron_expr:
            # 简单的验证
            if croniter.is_valid(cron_expr):
                logger.info(f"启动反思定时触发器，Cron: {cron_expr}")
                self._cron_task = asyncio.create_task(self._cron_loop(cron_expr))
            else:
                logger.error(f"无效的 Cron 表达式: {cron_expr}")

    async def _cron_loop(self, cron_expr: str):
        """定时任务无限循环"""
        while True:
            try:
                now = datetime.now()
                cron = croniter(cron_expr, now)
                next_time = cron.get_next(datetime)
                sleep_secs = (next_time - now).total_seconds()
                
                if sleep_secs > 0:
                    await asyncio.sleep(sleep_secs)
                    
                logger.info("达到定时反思触发时间，尝试触发...")
                try:
                    await self.trigger(trigger_type="MANUAL")
                except RuntimeError as e:
                    logger.warning(f"定时反思触发被拦截: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"定时反思循环异常: {e}")
                await asyncio.sleep(60) # 错误后休眠 1 分钟再试

    async def initialize(self) -> None:
        """初始化批次4专属元数据表"""
        try:
            await self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS reflection_sessions (
                    session_id TEXT PRIMARY KEY,
                    trigger_type TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    status TEXT NOT NULL,
                    report_path TEXT
                )
            """)
            await self.db.conn.commit()
            logger.info("反思会话元数据表 (reflection_sessions) 初始化完成")
            self.start_cron_scheduler()
        except Exception as e:
            logger.error("反思会话元数据表初始化失败: %s", e)
            raise
    async def trigger(self, trigger_type: Literal["MANUAL", "AUTO", "EVENT"] = "MANUAL") -> str:
        """触发并注册一个极低优先级的后台反思任务"""
        if self._is_collecting:
            logger.warning("当前系统正在进行深度反思，拒绝并发触发。")
            raise RuntimeError("系统反思正在进行中，请勿频繁触发。")
        # 为了避免重复查询，此处只写入状态锁即可，真正的采集延后至 TaskManager 调度
        session = ReflectionSession(trigger_type=trigger_type)
        report_file = self.report_dir / f"{session.session_id}_raw.json"
        session.report_path = str(report_file)
        await self.db.conn.execute(
            "INSERT INTO reflection_sessions (session_id, trigger_type, start_time, status, report_path) VALUES (?, ?, ?, ?, ?)",
            (session.session_id, session.trigger_type, session.start_time, session.status, session.report_path)
        )
        await self.db.conn.commit()
        # 托管至 Batch 1 的全局任务管理中心：极低优先级（1-7 刻度，7 最低）
        await self.task_manager.submit_task(ReflectionTask(
            task_id=session.session_id,
            priority=7,
            status="PENDING",
            session_id=session.session_id
        ))
        logger.info("[Reflection] 成功注册反思任务: %s (Type: %s)", session.session_id, trigger_type)
        return session.session_id
    async def process_task(self, task: ReflectionTask) -> None:
        """
        供 TaskManager 消费的长任务入口，真正负责执行极其耗时的全库审计与落地。
        此方法的执行结果作为后续 Extractor 模式挖掘的 RAW 粮草。
        """
        session_id = task.task_id
        logger.info("[Reflection] 开始执行底层全局状态采集审计...")
        
        self._is_collecting = True
        try:
            # 1. 采集统一原始特征池快照
            snapshot = await self.collector.collect_snapshot()
            
            # 2. 落地至物理文件，规避巨量 JSON 对内存与 Sqlite 的拖延
            report_file = Path(get_app_data_dir() / "reflection_reports" / f"{session_id}_raw.json")
            report_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            
            # 3. 完结会话
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                "UPDATE reflection_sessions SET status = 'COMPLETED', end_time = ? WHERE session_id = ?",
                (now, session_id)
            )
            await self.db.conn.commit()
            
            logger.info("[Reflection] 数据快照采集成功，落盘至: %s", report_file)

            # 4. 阶段2接线：快照落盘后立即挖掘（抽取器→应用器），
            # 终结"快照只写不读"的死胡同
            await self._mine_snapshot(snapshot, session_id)

        except Exception as e:
            logger.exception("[Reflection] 快照采集引发致命异常")
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                "UPDATE reflection_sessions SET status = 'FAILED', end_time = ? WHERE session_id = ?",
                (now, session_id)
            )
            await self.db.conn.commit()
            raise RuntimeError(f"反思数据采集核心发生崩溃: {e}") from e
        finally:
            self._is_collecting = False