"""
core/bootstrap.py — 应用生命周期分模块装配（架构优化）
========================================================
将原先堆叠在 main.py lifespan 中的约 200 行初始化逻辑拆分为
独立装配函数，职责单一、按需加载、失败可定位：

    setup_base              → 底座设施（DB / TaskManager / 建表巡检）
    setup_batch1_engine     → 批次1分段执行引擎全链路
    setup_llm_client        → 全局 LLM 适配器（密钥统一从 config_manager 读取）
    setup_reflection        → 反思系统（提取器 / 优化应用器 / 触发器）
    setup_quantification    → 量化核心（注册表 / 量化器 / 调度器 / 发散引擎）
    setup_control_center    → 总控中枢（状态 / 守卫 / 队列 / 路由器）
    start_background_tasks  → 后台协程托管
    create_lifespan         → 组装为 FastAPI lifespan 上下文管理器

main.py 仅保留：应用实例化 + 中间件 + 异常处理 + 路由挂载。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI

from core.config_manager import config_manager
from core.database import DatabaseManager
from core.startup_stages import StartupStage, run_stages, validate_stage_order
from core.task_manager import TaskManager
from core.path_resolver import get_temp_root
from core.security import is_cloud_enabled
from core.health import validate_startup_configuration

logger = logging.getLogger("ai_v4_bootstrap")

# 后台协程空闲轮询间隔（秒）
_IDLE_POLL_SECONDS = 0.2
# 反思自动巡检间隔（秒）
_AUTO_REFLECTION_INTERVAL = 3600
# 情感帧旧元数据归档巡检间隔（秒）
_EMOTION_ARCHIVE_INTERVAL = 3600


# ==========================================
# 底座设施
# ==========================================
async def setup_base(app: FastAPI) -> None:
    """装配数据库、任务管理器并完成建表巡检。"""
    db = DatabaseManager()
    await db.initialize()
    app.state.db = db

    task_manager = TaskManager(db)
    app.state.task_manager = task_manager

    await _init_database_tables(db)
    logger.info("[Bootstrap] 底座设施装配完成 (DB + TaskManager)")


async def _init_database_tables(db: DatabaseManager) -> None:
    """巡检并自动创建反思系统依赖库表（通用兜底）。"""
    await db.conn.execute("""
    CREATE TABLE IF NOT EXISTS reflection_sessions (
        session_id TEXT PRIMARY KEY,
        start_time REAL NOT NULL,
        end_time TEXT,
        status TEXT NOT NULL,
        trigger_type TEXT NOT NULL,
        report_path TEXT,
        summary TEXT
    )
    """)
    await db.conn.execute("""
    CREATE TABLE IF NOT EXISTS optimization_rules (
        rule_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        condition TEXT NOT NULL,
        action TEXT NOT NULL,
        confidence REAL NOT NULL,
        is_active INTEGER DEFAULT 1,
        created_at REAL NOT NULL,
        feedback_score REAL DEFAULT 0.0
    )
    """)
    await db.conn.execute("""
    CREATE TABLE IF NOT EXISTS universal_skills (
        skill_id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        applicability TEXT NOT NULL,
        source_cards TEXT,
        created_at REAL NOT NULL
    )
    """)
    await db.conn.commit()
    logger.info("[Bootstrap] 系统库表巡检完毕，状态正常。")


# ==========================================
# 批次1 分段执行引擎
# ==========================================
async def setup_batch1_engine(app: FastAPI) -> None:
    """装配 CommandSplitter → SegmentPipeline → tail 接力 → ResultMerger 全链路。"""
    from services.command_splitter import CommandSplitter
    from services.segment_pipeline import SegmentPipeline
    from services.result_merger import ResultMerger
    from services.temp_file_manager import TempFileManager
    from services.tail_context_manager import TailContextManager
    from services.task_manager import TaskManager as Batch1TaskManager

    db: DatabaseManager = app.state.db

    temp_manager = TempFileManager(
        temp_root=get_temp_root(),
        max_size_bytes=(
            config_manager.get_int("task.temp_max_size_gb", 2) * 1024 ** 3
            if config_manager.get("task.temp_max_size_gb", 2) else None
        ),
        cleanup_orphan_on_start=config_manager.get_bool(
            "task.cleanup_orphan_on_start", True
        ),
    )
    tail_manager = TailContextManager(
        temp_root=get_temp_root(),
        memory_threshold_bytes=config_manager.get_int(
            "task.tail_memory_threshold_bytes", 64 * 1024
        ),
        enable_disk_offload=config_manager.get_bool(
            "task.enable_tail_disk_offload", True
        ),
        ttl_seconds=config_manager.get("task.tail_ttl_seconds", 86400),
    )
    pipeline = SegmentPipeline(db, temp_manager, tail_manager=tail_manager)
    merger = ResultMerger()
    batch1_task_manager = Batch1TaskManager(
        db,
        CommandSplitter(),
        pipeline,
        merger,
        temp_manager,
        tail_manager=tail_manager,
    )
    await batch1_task_manager.initialize()

    app.state.batch1_task_manager = batch1_task_manager
    app.state.temp_file_manager = temp_manager
    app.state.tail_context_manager = tail_manager

    # 启动时扫描清理孤儿临时目录（保护非终态任务的分段目录）
    try:
        protected: set[str] = set()
        for active_row in await db.get_active_tasks():
            for seg_row in await db.get_segments_for_task(active_row["task_id"]):
                protected.add(seg_row["segment_id"])
        temp_manager.startup_cleanup(protected_segment_ids=protected)
        tail_manager.cleanup_expired()
    except Exception as cleanup_exc:
        logger.warning("[Bootstrap] 启动临时目录扫描异常（不阻塞启动）: %s", cleanup_exc)

    logger.info("[Bootstrap] 批次1分段执行引擎装配完成")


# ==========================================
# 全局 LLM 适配器
# ==========================================
async def setup_llm_client(app: FastAPI) -> None:
    """挂载全局 LLM 适配器（密钥统一从 config_manager 收敛读取）。

    安全门控：未启用开关或缺少密钥时 llm_client=None，云端功能整体禁用。
    """
    from utils.llm_adapter import DeepSeekClient

    llm_client: Any = None
    if is_cloud_enabled():
        llm_client = DeepSeekClient(
            api_key=config_manager.get_llm_api_key("deepseek"),
            api_base=config_manager.get_llm_base(
                "deepseek", default="https://api.deepseek.com/v1"
            ),
            model_name=config_manager.get_llm_model(
                "deepseek", default="deepseek-r1"
            ),
        )
        logger.info("[Bootstrap] 全局 LLM 适配器 (DeepSeek) 已开启挂载。")
    else:
        logger.info("[Bootstrap] 未启用云端开关或缺少凭据：云端功能禁用，走本地兜底。")

    app.state.llm_client = llm_client
    app.state.cloud_enabled = llm_client is not None


async def setup_web_access(app: FastAPI) -> None:
    """装配宿主控制的公网 HTTPS 抓取服务。"""
    from services.web_access import WebAccessService

    app.state.web_access_service = WebAccessService()
    logger.info("[Bootstrap] 受控公网抓取服务已装配")


# ==========================================
# 反思系统 (Batch 4)
# ==========================================
async def setup_reflection(app: FastAPI) -> None:
    """注入规则/技能提取器、优化应用器、卡片索引器与反思触发器。"""
    from extractors.rule_extractor import RuleExtractor, SkillExtractor
    from services.indexer import CardIndexer
    from services.optimization_applier import OptimizationApplier
    from services.project_manager import ProjectManager
    from services.reflection_trigger import DataCollector, ReflectionTrigger

    db: DatabaseManager = app.state.db
    task_manager: TaskManager = app.state.task_manager

    rule_extractor = RuleExtractor()
    skill_extractor = SkillExtractor()
    app.state.rule_extractor = rule_extractor
    app.state.skill_extractor = skill_extractor

    indexer = CardIndexer()
    await indexer.initialize()
    from services.ledger_repository import LedgerRepository
    from services.ledger_outbox import LedgerOutbox
    from services.ledger_read_facade import LedgerReadFacade
    from services.ledger_dual_read import LedgerDualRead
    from services.ledger_readiness import LedgerReadiness
    from services.skill_governance import SkillGovernance
    from services.skill_projection import SkillProjectionService
    app.state.ledger_repository = LedgerRepository(indexer)
    app.state.ledger_read_facade = LedgerReadFacade(indexer)
    app.state.ledger_dual_read = LedgerDualRead(indexer, app.state.ledger_read_facade)
    app.state.ledger_readiness = LedgerReadiness(indexer, app.state.ledger_dual_read)
    app.state.ledger_outbox = LedgerOutbox(indexer)
    await app.state.ledger_outbox.initialize()
    await app.state.ledger_outbox.recover_running()
    app.state.skill_governance = SkillGovernance()
    app.state.skill_projection_service = SkillProjectionService(db, indexer, app.state.skill_governance)
    # 旧投影与治理源对账服务（只读枚举始终可用，供开关关闭时观察缺口；
    # 写导入仅在 feature.novel_multi_agent_enable 开启时由 setup_novel_multi_agent 调用）
    from core.path_resolver import get_app_data_dir
    from services.skill_projection_reconcile import SkillProjectionReconciler

    app.state.skill_projection_reconcile = SkillProjectionReconciler(
        governance=app.state.skill_governance,
        indexer=indexer,
        app_data_dir=get_app_data_dir(),
        db=db,
    )

    applier = OptimizationApplier(db, skill_governance=app.state.skill_governance)
    await applier.initialize()
    app.state.optimization_applier = applier
    # 架构整改 1.1：ProjectManager 注入分支版本系统（开关关闭时自动回退旧逻辑）
    from services.version_control import VersionControlService

    version_control = VersionControlService(db)
    pm = ProjectManager(db, version_control=version_control)
    await pm.initialize()
    app.state.version_control = version_control
    app.state.project_manager = pm
    app.state.indexer = indexer

    collector = DataCollector(db, indexer, pm)
    trigger = ReflectionTrigger(db, task_manager, collector)
    await trigger.initialize()
    # 阶段2（学习环接线）：快照→抽取器→应用器 miners 注入，
    # 终结 RuleExtractor/SkillExtractor 实例化后无人调用、快照只写不读的状态
    trigger.attach_miners(rule_extractor, skill_extractor, applier)
    app.state.reflection_trigger = trigger

    # 阶段4集成A：锁定场服务单例（供 dispatcher 稳定前缀与 API 共享 must 集缓存）
    try:
        from services.lockfield import LockFieldService

        lockfield_service = LockFieldService(db, indexer)
        await lockfield_service.initialize()
        app.state.lockfield_service = lockfield_service
    except Exception as exc:
        logger.warning("[Bootstrap] 锁定场服务初始化失败（跳过，不阻断）: %s", exc)

    # 【0-2 修复】将全局 LLM 客户端注入反思引擎，去除硬编码反思结论。
    # 未启用云端开关时 llm_client 为 None，ReflectionEngine 自动降级，不影响既有链路。
    try:
        from services.reflection_engine import reflection_engine

        reflection_engine.set_model_client(getattr(app.state, "llm_client", None))
    except Exception as exc:
        logger.warning("[Bootstrap] 注入反思引擎模型客户端失败，跳过: %s", exc)

    logger.info("[Bootstrap] 反思系统装配完成")


# ==========================================
# 第十部分: 多智能体小说创作 自学习闭环
# ==========================================
async def setup_novel_multi_agent(app: FastAPI) -> None:
    """装配多智能体小说创作总监督管、审计仓库、技能仓库与自学习闭环。

    受 feature.novel_multi_agent_enable 总开关控制；
    关闭时全部服务缺省 None，多智能体功能整体禁用、不影响其它模块。
    """
    from core.config_manager import config_manager

    enable = config_manager.get_bool("feature.novel_multi_agent_enable", False)
    if not enable:
        logger.info("[Bootstrap] feature.novel_multi_agent_enable=false，跳过多智能体装配")
        return

    from models.novel_agent import NovelAgentSkill
    from services.novel_agent_audit_store import NovelAgentAuditStore
    from services.novel_agent_skill_store import NovelAgentSkillStore
    from services.novel_agent_learning_loop import NovelAgentLearningLoop
    from services.novel_supervisor import NovelSupervisor

    db: DatabaseManager = app.state.db
    task_manager: TaskManager = app.state.task_manager
    indexer = app.state.indexer
    applier = app.state.optimization_applier

    # 1. 审计仓库
    audit_store = NovelAgentAuditStore(db)
    await audit_store.initialize()
    app.state.novel_agent_audit_store = audit_store

    # 2. 技能仓库
    skill_store = NovelAgentSkillStore(db, indexer=indexer)
    await skill_store.initialize()
    app.state.novel_agent_skill_store = skill_store

    # 3. 自学习闭环
    learning_loop = NovelAgentLearningLoop(
        db=db,
        audit_store=audit_store,
        skill_store=skill_store,
        optimization_applier=applier,
        task_manager=task_manager,
        indexer=indexer,
        skill_governance=getattr(app.state, "skill_governance", None),
    )
    app.state.novel_agent_learning_loop = learning_loop

    # 4. 多智能体总监督管（注入批次1任务管理器与审计/技能仓库）
    supervisor = NovelSupervisor(
        indexer=indexer,
        optimization_applier=applier,
        dispatcher=app.state.model_dispatcher if hasattr(app.state, "model_dispatcher") else None,
        divergent_engine=app.state.divergent_engine if hasattr(app.state, "divergent_engine") else None,
        skill_governance=getattr(app.state, "skill_governance", None),
    )
    supervisor._batch1_task_manager = getattr(app.state, "batch1_task_manager", None)
    supervisor._audit_store = audit_store
    supervisor._skill_store = skill_store
    app.state.novel_supervisor = supervisor

    # 旧投影与治理源全量对账（写路径：未映射旧技能幂等导入为治理候选）。
    # 失败不阻断启动，仅告警记录；开关关闭时由 setup_reflection 装配的对账服务
    # 只允许只读的 list_projection_gaps（观察缺口），不执行本写导入。
    reconciler = getattr(app.state, "skill_projection_reconcile", None)
    if reconciler is not None:
        try:
            pr_report = await reconciler.reconcile()
            logger.info(
                "[Bootstrap] 旧投影对账完成: total=%s imported=%d already_migrated=%d changed=%d errors=%d",
                pr_report.get("total_legacy"),
                len(pr_report.get("imported_candidates", [])),
                len(pr_report.get("already_migrated", [])),
                len(pr_report.get("changed_after_migration", [])),
                len(pr_report.get("errors", [])),
            )
        except Exception as exc:
            logger.warning("[Bootstrap] 旧投影对账失败（不阻断启动）: %s", exc)

    logger.info("[Bootstrap] 多智能体小说创作闭环装配完成")


# ==========================================
# 量化核心 (Batch 2) 与发散引擎
# ==========================================
async def setup_quantification(app: FastAPI) -> None:
    """装配卡片注册表、量化器、模型调度器与发散引擎。"""
    from services.quantifier import BookQuantifier
    from services.card_registry import CardTypeRegistry
    from strategies.extraction import DefaultStrategy
    from services.dispatcher import ModelDispatcher
    from services.divergent_engine import DivergentEngine

    db: DatabaseManager = app.state.db
    task_manager: TaskManager = app.state.task_manager
    indexer = app.state.indexer
    pm = app.state.project_manager

    registry = CardTypeRegistry()
    registry.register_default_types()

    # 阶段4集成A（讨论稿第六章）：锁定场 must 集 → dispatcher 稳定前缀。
    # 项目绑定锁定场时，史实硬约束卡以确定性顺序置于上下文最前
    # （环境锁定 + DeepSeek 前缀缓存命中率）；无锁定场/异常时静默降级为空。
    async def _lockfield_prefix(project_id: str | None) -> str:
        if not project_id:
            return ""
        svc = getattr(app.state, "lockfield_service", None)
        if svc is None:
            return ""
        config = await svc.get_field(project_id)
        if config is None:
            return ""
        must = await svc.materialize_must_set(config)
        if must.total == 0:
            return ""
        lines = [
            f"- {card.get('content') or card.get('summary') or ''}".rstrip()
            for card in must.cards
        ]
        lines = [ln for ln in lines if ln != "-"]
        if not lines:
            return ""
        return "【世界观锁定·史实基线(硬约束,不可违背)】\n" + "\n".join(lines) + "\n\n"

    dispatcher = ModelDispatcher(
        task_manager, indexer, pm, lockfield_prefix_provider=_lockfield_prefix
    )
    # 提取策略：LLM 优先 + 规则兜底（默认 auto）。
    # LLMExtractionStrategy 调用真实大模型提取；无模型/异常时 FallbackStrategy
    # 自动回退 DefaultStrategy 规则提取，保证量化永不中断。
    from strategies.extraction import DefaultStrategy, FallbackStrategy
    from strategies.llm_extraction import LLMExtractionStrategy

    rule_strategy = DefaultStrategy(registry=registry)
    llm_strategy = LLMExtractionStrategy(registry=registry, dispatcher=dispatcher)
    strategy = FallbackStrategy(
        registry=registry, primary=llm_strategy, fallback=rule_strategy
    )
    # P1-1.4：量化器复用批次1引擎的 TailContextManager（双模式落盘统一）
    quantifier = BookQuantifier(
        db, task_manager, indexer, registry, strategy,
        tail_manager=getattr(app.state, "tail_context_manager", None),
    )

    from services.code_execution import CodeExecutionService
    code_service = CodeExecutionService(db=db)
    app.state.code_execution_service = code_service

    # 原先只装配了 quantifier 却从未调用 task_manager.start_workers，
    # 导致 submit_quantize_task 提交后任务永久停留在 PENDING，
    # 且重复量化保护会让后续所有量化请求 409 死锁（系统功能瘫痪）。
    # 文档学习任务（learn_document_*）与量化共用该 worker，因此使用
    # 分派 handler 按 raw_command 前缀路由到对应引擎；
    # learning_engine 在 setup_control_center 阶段装配，handler 内懒获取。
    # 并发数 =1：重负载任务串行执行，与 library.max_concurrent_quantize
    # 排队语义一致；start_workers 带 _is_running 防重入。
    from models.task import BasePipelineTask
    async def _task_dispatcher(task: BasePipelineTask) -> None:
        if task.task_type == "code_execution":
            from models.code_execution import CodePlan, CodeTaskRequest
            request = CodeTaskRequest.model_validate(getattr(task, "request_payload", {}))
            plan_payload = dict(getattr(task, "plan_payload", {}) or {})
            plan = CodePlan.model_validate({key: value for key, value in plan_payload.items() if key in CodePlan.model_fields})
            plan = code_service.approve(plan, str(plan_payload.get("approval_id", "worker")))
            await code_service.execute(request, plan, task_id=task.task_id)
            return
        if task.task_type == "learning":
            engine = getattr(app.state, "learning_engine", None)
            if engine is not None:
                await engine.process_task(task)
            else:
                logger.warning("[Bootstrap] 学习任务被消费但 learning_engine 未装配，跳过: %s", task.task_id)
            return
        if task.task_type == "reflection":
            # 阶段2修复：novellearn_ 自学习反思任务直接走学习环，
            # 不再先跑一遍全库快照（原先每次多智能体创作后都重复
            # 拍全量快照，纯浪费 IO，且与学习环自身的审计分析重复）
            if getattr(task, "task_id", "").startswith("novellearn_"):
                loop = getattr(app.state, "novel_agent_learning_loop", None)
                if loop is not None:
                    try:
                        await loop.process_reflection_task()
                    except Exception as exc:
                        logger.error("[Bootstrap] 多智能体反思学习执行失败: %s", exc)
                else:
                    logger.warning(
                        "[Bootstrap] novellearn_ 任务被消费但学习环未装配，跳过: %s",
                        task.task_id,
                    )
                return
            trigger = getattr(app.state, "reflection_trigger", None)
            if trigger is not None:
                await trigger.process_task(task)
            return
        if task.task_type == "generate_image":
            # 补丁4：分镜生图任务（StoryboardService 懒装配）
            svc = getattr(app.state, "storyboard_service", None)
            if svc is None:
                from services.storyboard import StoryboardService

                svc = StoryboardService(
                    app.state.project_manager,
                    task_manager=task_manager,
                )
                app.state.storyboard_service = svc
            payload = getattr(task, "payload", None) or {}
            anchor_id = payload.get("anchor_id", "")
            project_id = payload.get("project_id", "")
            if anchor_id and project_id:
                await svc.process_generate(project_id, anchor_id, task.raw_command)
            else:
                logger.warning("[Bootstrap] 生图任务缺少锚点上下文: %s", task.task_id)
            return
        if task.task_type == "deep_think":
            # 批次7：创作长思考流水线（DeepThinkService 懒装配）
            svc = getattr(app.state, "deep_think_service", None)
            if svc is None:
                from services.deep_think import DeepThinkService

                svc = DeepThinkService(
                    task_manager=task_manager,
                    project_manager=pm,
                    dispatcher=dispatcher,
                )
                app.state.deep_think_service = svc
            try:
                await svc.process_task(task)
            except Exception as exc:
                logger.error("[Bootstrap] 长思考流水线执行失败: %s", exc)
            return
        await quantifier.process_task(task)

    # 原先只装配了 quantifier 却从未调用 task_manager.start_workers，这里补全
    # 排队语义一致；start_workers 有 _is_running 防重入。
    from core.agent_runtime_patch import apply_agent_runtime_patch
    patched_dispatcher = apply_agent_runtime_patch(_task_dispatcher)
    await task_manager.start_workers(patched_dispatcher, concurrency=1)
    logger.info("[Bootstrap] 核心任务 Worker 已启动 (concurrency=1, 量化/文档学习分派)")

    divergent_engine = DivergentEngine(dispatcher, indexer)

    app.state.registry = registry
    app.state.quantifier = quantifier
    app.state.model_dispatcher = dispatcher
    app.state.divergent_engine = divergent_engine

    # 阶段1（主链路真实化，讨论稿20260816第三章修复顺序第1步）：
    # 为批次1分段流水线注入真实模型执行钩子，终结长任务创作的占位符输出。
    # dispatcher 经延迟 getter 解引用，与 setup_batch1_engine 的装配顺序解耦。
    from services.segment_execution_hook import build_model_execution_hook
    batch1_tm = getattr(app.state, "batch1_task_manager", None)
    if batch1_tm is not None:
        batch1_tm.pipeline.set_execution_hook(
            build_model_execution_hook(
                lambda: getattr(app.state, "model_dispatcher", None)
            )
        )
        logger.info("[Bootstrap] 批次1分段执行钩子已接入真实模型调度")

    logger.info("[Bootstrap] 量化核心与发散引擎装配完成")


# ==========================================
# 总控中枢 (Batch 5)
# ==========================================
async def setup_control_center(app: FastAPI) -> None:
    """装配状态管理器、文件守卫、优先队列、监控器与全局路由器。"""
    from core.state_manager import StateManager
    from guards.fs_guard import FileSystemGuard
    from services.priority_queue import PriorityTaskQueue
    from services.system_monitor import SystemMonitor
    from services.global_router import GlobalRouter
    from services.learning_engine import DocumentLearningEngine
    from services.load_estimator import LoadEstimator

    db: DatabaseManager = app.state.db
    task_manager: TaskManager = app.state.task_manager
    pm = app.state.project_manager
    indexer = app.state.indexer
    dispatcher = app.state.model_dispatcher
    divergent_engine = app.state.divergent_engine
    trigger = app.state.reflection_trigger
    applier = app.state.optimization_applier
    batch1_task_manager = app.state.batch1_task_manager

    state_manager = StateManager()
    app.state.state_manager = state_manager

    fs_guard = FileSystemGuard()
    app.state.fs_guard = fs_guard

    priority_task_queue = PriorityTaskQueue()
    app.state.priority_task_queue = priority_task_queue

    system_monitor = SystemMonitor()
    app.state.system_monitor = system_monitor

    # 补丁E剩余业务：GC 后台任务管理器（空闲 VACUUM + 临时清理）
    from core.gc_manager import GCTaskManager

    gc_task_manager = GCTaskManager(
        db=db,
        system_monitor=system_monitor,
        temp_manager=getattr(app.state, "temp_file_manager", None),
        tail_manager=getattr(app.state, "tail_context_manager", None),
        state_manager=state_manager,
    )
    app.state.gc_task_manager = gc_task_manager

    learning_engine = DocumentLearningEngine(
        db=db,
        task_manager=task_manager,
        project_manager=pm,
        dispatcher=dispatcher,
    )
    app.state.learning_engine = learning_engine

    load_estimator = LoadEstimator(indexer=indexer, project_manager=pm)
    app.state.load_estimator = load_estimator

    global_router = GlobalRouter(
        task_queue=priority_task_queue,
        system_monitor=system_monitor,
        task_manager=task_manager,
        reflection_trigger=trigger,
        optimization_applier=applier,
        model_dispatcher=dispatcher,
        project_manager=pm,
        batch1_task_manager=batch1_task_manager,
        db=db,
        load_estimator=load_estimator,
        divergent_engine=divergent_engine,
        indexer=indexer,
        # 第十部分：多智能体小说创作总监督管（开关关闭时为 None）
        novel_supervisor=getattr(app.state, "novel_supervisor", None),
    )
    # 第十部分：多智能体自学习闭环引用（创作完成后触发低优先级反思任务）
    global_router._novel_learning_loop = getattr(
        app.state, "novel_agent_learning_loop", None
    )
    app.state.global_router = global_router

    logger.info("[Bootstrap] 总控中枢装配完成")


# ==========================================
# 后台协程托管
# ==========================================
async def _auto_reflection_worker(app: FastAPI) -> None:
    """后台轮询守护进程：定时自动唤醒 ReflectionTrigger。"""
    from services.reflection_trigger import ReflectionTrigger

    trigger: ReflectionTrigger = app.state.reflection_trigger
    while True:
        try:
            await asyncio.sleep(_AUTO_REFLECTION_INTERVAL)
            logger.info("[AutoReflection] 触发系统自动反思巡检...")
            await trigger.trigger(trigger_type="AUTO")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[AutoReflection] 自动巡检发生异常: %s", exc)


async def _emotion_archive_worker(app: FastAPI) -> None:
    """补丁G后台协程：周期性归档旧情感帧元数据（磁盘 JSON 保留为冷数据）。"""
    while True:
        try:
            await asyncio.sleep(_EMOTION_ARCHIVE_INTERVAL)
            db = getattr(app.state, "db", None)
            if db is None:
                continue
            from services.emotion_engine.frame_manager import EmotionFrameManager

            frame_manager = EmotionFrameManager(db)
            await frame_manager.prune_old_frame_meta()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[EmotionArchive] 帧归档巡检异常: %s", exc)


# 批次4:空闲索引器轮询间隔
_IDLE_INDEX_INTERVAL = 300  # 5 分钟

async def _idle_indexer_worker(app: FastAPI) -> None:
    """批次4:空闲索引后台协程。

    静默期(文档变更后5分钟)+系统空闲时,把变更文档蒸馏为摘要卡/
    实体初筛/矛盾巡检,产物一律 draft 等待作者审核。开关
    feature.idle_index_enable 关闭时整周期跳过,不产生任何文件。
    """
    while True:
        try:
            await asyncio.sleep(_IDLE_INDEX_INTERVAL)
            if not config_manager.get_bool("feature.idle_index_enable", True):
                continue
            pm = getattr(app.state, "project_manager", None)
            if pm is None:
                continue
            from services.idle_indexer import is_system_idle, run_idle_index_cycle

            if not await is_system_idle(getattr(app.state, "batch1_task_manager", None)):
                logger.info("[IdleIndexer] 系统忙碌,本次跳过空闲索引")
                continue
            result = await run_idle_index_cycle(
                pm,
                indexer=getattr(app.state, "indexer", None),
                ensemble_svc=getattr(app.state, "ensemble_service", None),
            )
            if result["indexed"]:
                logger.info("[IdleIndexer] 本次索引 %d 个文档(跳过 %d, 失败 %d)",
                            result["indexed"], result["skipped"], result["failures"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[IdleIndexer] 空闲索引周期异常: %s", exc)


async def _batch1_engine_worker(app: FastAPI) -> None:
    """批次1分段执行引擎的后台消费者（空闲时 sleep 避免忙等）。

    长任务流式输出：每个任务生命周期节点通过 WS manager 发布 task_progress 事件，
    前端订阅任务频道即可实时接收状态与分段进度。
    """
    from api.websocket import manager as ws_manager

    tm = app.state.batch1_task_manager
    while True:
        try:
            task = await tm.process_next()
            if task is None:
                await asyncio.sleep(_IDLE_POLL_SECONDS)
                continue

            # 发布任务进度事件（仅当存在订阅者时才有网络开销）
            try:
                await ws_manager.publish_task_event(
                    task.task_id,
                    {
                        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                        "segments": [
                            {
                                "segment_id": s.segment_id,
                                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                                "sequence_order": s.sequence_order,
                            }
                            for s in task.segments
                        ],
                    },
                )
            except Exception as ws_exc:
                logger.warning("[Batch1Worker] 发布任务进度失败: %s", ws_exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[Batch1Worker] 分段引擎消费任务异常: %s", exc)
            await asyncio.sleep(0.5)


async def _ledger_outbox_worker(app: FastAPI) -> None:
    """Drain Ledger compatibility events without changing authoritative mode."""
    outbox = getattr(app.state, "ledger_outbox", None)
    if outbox is None:
        return
    while True:
        try:
            result = await outbox.drain_compat(limit=100)
            if result["applied"] or result["failed"] or result["dead_letter"]:
                logger.info("[LedgerOutbox] drain=%s", result)
            await asyncio.sleep(1.0 if result["applied"] else 5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[LedgerOutbox] 消费异常: %s", exc)
            await asyncio.sleep(5.0)


async def _queue_worker(app: FastAPI) -> None:
    """后台轮询守护进程：消费 priority_task_queue 中的任务。"""
    from services.priority_queue import PriorityTaskQueue

    queue: PriorityTaskQueue = app.state.priority_task_queue
    while True:
        task_id = None
        try:
            task_id, queued_task = await queue.pop()
            logger.info("[QueueWorker] 开始执行任务 %s", task_id)
            # 优先使用延迟工厂：只有任务真正出队后才创建 coroutine。
            coro = queued_task() if callable(queued_task) else queued_task
            await coro
            logger.info("[QueueWorker] 任务 %s 执行完毕", task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[QueueWorker] 处理任务 %s 时发生异常", task_id or "unknown")
        finally:
            if task_id is not None:
                try:
                    queue.task_done()
                except Exception:
                    logger.exception("[QueueWorker] task_done 失败: %s", task_id)


async def _supervised_background_worker(
    name: str, worker_factory: Any, restart_delay: float = 1.0
) -> None:
    """后台协程总入口隔离器：异常完整落日志，退出后自动重启。"""
    while True:
        try:
            await worker_factory()
            logger.error("[Bootstrap] 后台协程 %s 意外退出，将在 %.1fs 后重启", name, restart_delay)
        except asyncio.CancelledError:
            raise
        except BaseException:
            # BaseException 覆盖第三方后台代码误抛的非 Exception 异常；取消仍单独放行。
            logger.exception("[Bootstrap] 后台协程 %s 崩溃，主事件循环保持运行", name)
        await asyncio.sleep(restart_delay)


def start_background_tasks(app: FastAPI) -> None:
    """托管全部后台协程。

    线程池评估结论：三个协程均为 asyncio 原生 I/O 密集（轮询、队列、DB 异步），
    不涉及 CPU 阻塞调用，无需 to_thread 线程池隔离；保持事件循环内运行即可，
    避免线程切换开销。CPU 密集型（大文档解析）已由批次1引擎独立消费。
    P2-2.2：SystemMonitor 后台采样协程（临时目录全量遍历移出请求路径）。
    """
    app.state.bg_reflection_task = asyncio.create_task(
        _supervised_background_worker("reflection", lambda: _auto_reflection_worker(app))
    )
    app.state.bg_ledger_outbox_task = asyncio.create_task(
        _supervised_background_worker("ledger-outbox", lambda: _ledger_outbox_worker(app))
    )
    app.state.bg_queue_task = asyncio.create_task(
        _supervised_background_worker("priority-queue", lambda: _queue_worker(app))
    )
    app.state.bg_batch1_task = asyncio.create_task(
        _supervised_background_worker("batch1", lambda: _batch1_engine_worker(app))
    )
    app.state.bg_emotion_archive_task = asyncio.create_task(
        _supervised_background_worker("emotion-archive", lambda: _emotion_archive_worker(app))
    )
    app.state.bg_idle_index_task = asyncio.create_task(
        _supervised_background_worker("idle-index", lambda: _idle_indexer_worker(app))
    )
    monitor = getattr(app.state, "system_monitor", None)
    if monitor is not None:
        try:
            monitor.start_background_sampler()
        except Exception as exc:
            logger.warning("[Bootstrap] SystemMonitor 后台采样启动失败: %s", exc)
    logger.info("[Bootstrap] 后台协程托管完毕 (reflection / queue / batch1 / emotion-archive / monitor)")


async def stop_background_tasks(app: FastAPI) -> None:
    """按依赖逆序停止后台任务并释放全部运行时资源。

    关闭期间必须先取消 supervisor，再停止它所依赖的 worker；否则
    supervisor 会把正常的取消误判成“意外退出”并重新创建 worker，导致
    TestClient/uvicorn 重启时残留任务和数据库连接。
    """
    for name in (
        "bg_reflection_task",
        "bg_ledger_outbox_task",
        "bg_queue_task",
        "bg_batch1_task",
        "bg_emotion_archive_task",
    ):
        task = getattr(app.state, name, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    monitor = getattr(app.state, "system_monitor", None)
    if monitor is not None:
        try:
            await monitor.stop_background_sampler()
        except Exception:
            pass

    gc_manager = getattr(app.state, "gc_task_manager", None)
    if gc_manager is not None:
        try:
            await gc_manager.shutdown()
        except Exception:
            pass

    indexer = getattr(app.state, "indexer", None)
    if indexer:
        try:
            await indexer.close()
        except Exception:
            pass

    core_task_manager = getattr(app.state, "task_manager", None)
    if core_task_manager is not None:
        try:
            await core_task_manager.stop_workers()
        except Exception:
            logger.warning("[Bootstrap] 核心任务 Worker 停止失败", exc_info=True)

    # batch1 当前由 bg_batch1_task 消费，不拥有独立 worker；保留可选
    # shutdown/close 钩子以兼容未来实现，避免生命周期依赖具体版本。
    batch1_task_manager = getattr(app.state, "batch1_task_manager", None)
    for method_name in ("shutdown", "close"):
        method = getattr(batch1_task_manager, method_name, None)
        if method is not None:
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.warning("[Bootstrap] 批次1任务管理器 %s 失败", method_name, exc_info=True)
            break

    db = getattr(app.state, "db", None)
    if db is not None:
        try:
            await db.close()
        except Exception:
            logger.warning("[Bootstrap] 数据库关闭失败", exc_info=True)

    # 兜底关闭仍保留：其它服务可能直接持有默认池连接。
    from core.db_pool import db_pool
    await db_pool.close_all()

    logger.info("[Bootstrap] 全局资源释放完毕，安全退出。")


# ==========================================
# 生命周期组装
# ==========================================
async def initialize_app(app: FastAPI) -> None:
    """按序执行全部装配阶段（任一阶段失败即中断启动，fail fast）。"""
    logger.info("====== 正在启动 No.0 AI V4.0 系统 ======")
    app.state.initialization_complete = False
    app.state.initialization_error = None
    configuration_problems = validate_startup_configuration()
    if configuration_problems:
        logger.error("[Bootstrap] 启动配置校验失败: %s", "; ".join(configuration_problems))
        app.state.initialization_error = "startup_configuration_invalid"
        raise RuntimeError("启动配置校验失败，请检查生产环境安全配置")
    stages = build_startup_stages()
    order_problems = validate_stage_order(stages)
    if order_problems:
        # 装配顺序声明错误属编码缺陷,启动即暴露而非运行时才炸
        logger.error("[Bootstrap] 启动阶段顺序声明有误: %s", "; ".join(order_problems))
        app.state.initialization_error = "startup_stage_order_invalid"
        raise RuntimeError("启动阶段顺序声明有误: " + "; ".join(order_problems))

    degraded = await run_stages(app, stages)
    app.state.degraded_stages = degraded

    if config_manager.get_bool("ledger.authoritative", False):
        readiness = getattr(app.state, "ledger_readiness", None)
        if readiness is None:
            app.state.initialization_error = "ledger_readiness_unavailable"
            raise RuntimeError("Ledger authoritative 门禁服务未装配，拒绝启动")
        report = await readiness.report()
        if not report.get("ready_for_authoritative", False):
            app.state.initialization_error = "ledger_authoritative_not_ready"
            raise RuntimeError("Ledger authoritative 门禁未通过: " + "; ".join(report.get("blockers", [])))

    start_background_tasks(app)
    app.state.initialization_complete = True
    if degraded:
        logger.warning(
            "系统初始化完成(部分增强阶段降级: %s),核心底座处于 Standby 状态。",
            ", ".join(degraded),
        )
    else:
        logger.info("系统初始化完成，全部路由与底座处于 Standby 状态。")


# ── 可选收尾阶段(失败仅降级,不阻断启动) ─────────────────────────

async def _stage_prompt_audit(app: FastAPI) -> None:
    """架构整改 1.3：硬编码 Prompt 启动自检。"""
    from core.prompt_audit import run_prompt_audit

    run_prompt_audit()


async def _stage_blueprint(app: FastAPI) -> None:
    """批次7：生成系统架构镜像(architecture_map/api_spec/ui_flow)。"""
    if not config_manager.get_bool("feature.deep_thinking_enable", False):
        return
    from core.blueprint import blueprint_generator

    blueprint_generator.generate_all(app)


async def _stage_orphan_cleanup(app: FastAPI) -> None:
    """回收发散引擎等孤儿临时目录(按 mtime TTL 淘汰)。"""
    from core.temp_manager import temp_manager as scoped_temp_manager

    cleaned = scoped_temp_manager.cleanup_orphans(function_type="divergence")
    if cleaned:
        logger.info("[Bootstrap] 启动回收发散引擎孤儿临时目录 %d 个", cleaned)


async def _stage_idle_index_catchup(app: FastAPI) -> None:
    """批次4:启动欠账补做——上次索引落后于文档修改的项目排队补建。

    非 critical:失败仅降级,不阻断启动;开启开关才执行。
    """
    if not config_manager.get_bool("feature.idle_index_enable", True):
        return
    pm = getattr(app.state, "project_manager", None)
    if pm is None:
        return
    try:
        from services.idle_indexer import run_idle_index_cycle

        result = await run_idle_index_cycle(
            pm,
            indexer=getattr(app.state, "indexer", None),
        )
        if result["indexed"]:
            logger.info("[Bootstrap] 启动欠账补做:索引 %d 个文档", result["indexed"])
    except Exception as exc:
        logger.warning("[Bootstrap] 启动欠账补做失败(降级继续): %s", exc)


def build_startup_stages() -> list[StartupStage]:
    """启动阶段声明表:顺序、失败语义与依赖关系一目可见。"""
    return [
        StartupStage("base", setup_base, note="DB + 持久化任务队列"),
        StartupStage("batch1_engine", setup_batch1_engine, depends_on=("base",),
                     note="拆分器→分段管线→合并器"),
        StartupStage("llm_client", setup_llm_client, note="云端适配器(未启用则 None)"),
        StartupStage("web_access", setup_web_access, note="宿主受控公网 HTTPS 抓取"),
        StartupStage("reflection", setup_reflection, depends_on=("base",),
                     note="抽取器/应用器/索引器/锁定场"),
        StartupStage("quantification", setup_quantification,
                     depends_on=("base", "reflection", "batch1_engine"),
                     note="量化核心+模型调度器+分段执行钩子"),
        StartupStage("novel_multi_agent", setup_novel_multi_agent,
                     depends_on=("quantification", "reflection"),
                     note="多智能体闭环(feature 门控)"),
        StartupStage("control_center", setup_control_center,
                     depends_on=("quantification", "reflection"),
                     note="状态机/队列/监控/全局路由"),
        StartupStage("prompt_audit", _stage_prompt_audit, critical=False,
                     note="硬编码 Prompt 自检"),
        StartupStage("blueprint", _stage_blueprint, critical=False,
                     note="架构镜像生成"),
        StartupStage("orphan_cleanup", _stage_orphan_cleanup, critical=False,
                     note="孤儿临时目录回收"),
        StartupStage("idle_index_catchup", _stage_idle_index_catchup, critical=False,
                     depends_on=("control_center",),
                     note="空闲索引欠账补做(非阻断)"),
    ]


async def shutdown_app(app: FastAPI) -> None:
    logger.info("====== 正在关闭 No.0 AI V4.0 系统 ======")
    await stop_background_tasks(app)


def create_lifespan() -> Any:
    """构造 FastAPI lifespan 上下文管理器。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await initialize_app(app)
        try:
            yield
        finally:
            await shutdown_app(app)

    return lifespan
