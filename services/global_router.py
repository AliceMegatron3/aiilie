"""
services/global_router.py — 统一指令识别与调度决策大脑
===================================================
全局的中央处理器，接收外部原始命令（NLP自然语言或简单结构化指令），
融合 SystemMonitor 资源探测结果，与 OptimizationApplier 的动态规则，
决策究竟分配到哪一个批次的哪个模块执行。

P0 修复（本批次）：打通与批次1分段执行引擎的集成断层
===================================================
- 长任务（创作 / 书籍量化 / 深度思考）不再生成 _lazy_creation 闭包任务，
  而是构造标准 CommandTask，直接提交给批次1 services.task_manager.TaskManager
  （submit_task），完整走 CommandSplitter 拆分 → SegmentPipeline 分段执行 →
  tail 尾巴接力 → ResultMerger 流式合并 全链路；
- 短任务保留原有简易闭包逻辑做兼容；
- 原有 PriorityTaskQueue 保留为上层适配层（量化占位/回退模式仍在其中转发）；
- 兼容开关 global_router.use_batch1_engine：关闭后切回旧闭包队列，用于回退调试。
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable
from models.system import CommandRequest, PriorityLevel
from models.task import CommandTask, normalize_segment_strategy
from services.command_splitter import (
    SEGMENT_STRATEGY_AUTO,
    SEGMENT_STRATEGY_FORCE_SPLIT,
    SEGMENT_STRATEGY_NO_SPLIT,
)
from services.priority_queue import PriorityTaskQueue
from services.system_monitor import SystemMonitor
from services.optimization_applier import OptimizationApplier
from services.reflection_trigger import ReflectionTrigger
from core.task_manager import TaskManager
from services.dispatcher import ModelDispatcher
from models.cards import DataCard, InfoCard
from models.library import LibraryCatalog, parse_library_command

from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# 批次1 优先级契约（1最高-7最低）←→ 批次5 PriorityLevel 的映射
_PRIORITY_LEVEL_TO_BATCH1 = {
    PriorityLevel.LV7: 1,
    PriorityLevel.LV6: 2,
    PriorityLevel.LV4: 4,
    PriorityLevel.LV1: 7,
}

# 深度思考模式标记：命中即视为长任务走批次1引擎
_DEEP_THINK_MODES = {"think", "deep", "reasoning", "deepseek-r1"}

# 默认长任务字数阈值（与 CommandSplitter 的 DEFAULT_CHAR_THRESHOLD 对齐）
DEFAULT_LONG_COMMAND_MIN_CHARS = 2000

class IntentDetector(ABC):
    @abstractmethod
    def detect(self, cmd_text: str) -> str:
        pass

class KeywordIntentDetector(IntentDetector):
    def detect(self, cmd_text: str) -> str:
        if any(kw in cmd_text for kw in ["新建项目", "创建项目"]):
            return "PROJECT_MANAGEMENT"
        elif any(kw in cmd_text for kw in ["调用资料", "调用历史资料", "查资料", "检索资料", "调用卡片", "查找卡片", "创建卡片", "新建卡片", "维护卡片", "更新卡片", "关联卡片", "审计书库", "审计资料库"]):
            return "LIBRARY"
        elif any(kw in cmd_text for kw in ["反思", "复盘", "自省", "学习分析"]):
            return "REFLECTION"
        elif any(kw in cmd_text for kw in ["量化", "入库", "解析卡片"]):
            return "QUANTIZATION"
        # 补丁：新增文档学习意图（短指令 + 大文档后台重负载）
        elif any(kw in cmd_text for kw in ["文档学习", "学习文档", "解析文档"]):
            return "DOC_LEARNING"
        return "CREATION"

class GlobalRouter:
    """中枢路由器"""

    def __init__(
        self,
        task_queue: PriorityTaskQueue,
        system_monitor: SystemMonitor,
        task_manager: TaskManager,
        reflection_trigger: ReflectionTrigger,
        optimization_applier: OptimizationApplier,
        model_dispatcher: ModelDispatcher = None,
        project_manager = None,
        batch1_task_manager = None,
        load_estimator = None,
        divergent_engine = None,
        novel_supervisor = None,
        indexer = None,
        db = None,
    ) -> None:
        self.task_queue = task_queue
        self.system_monitor = system_monitor
        self.task_manager = task_manager
        self.reflection_trigger = reflection_trigger
        self.optimization_applier = optimization_applier
        self.model_dispatcher = model_dispatcher
        self.project_manager = project_manager
        # P0：批次1分段执行引擎（services.task_manager.TaskManager），
        # 由 core/bootstrap.py 装配注入；为 None 时自动回退旧闭包队列逻辑。
        self.batch1_task_manager = batch1_task_manager
        # B1-09：指令负载预估器（services.load_estimator.LoadEstimator），
        # 为 None 时跳过自动预估（回退补丁前行为）。
        self.load_estimator = load_estimator
        # Phase 13：发散引擎（构造函数显式注入，替代原先实例化后手动挂载）
        self.divergent_engine = divergent_engine
        # 第十部分：多智能体小说创作总监督管（feature 开关关闭时为 None）
        self.novel_supervisor = novel_supervisor
        self.indexer = indexer
        # 阶段3集成B:叙事结构层生成简报依赖(为 None 时跳过简报注入)
        self._db = db

        # Repetition block history: cmd_text -> list of timestamps
        self._command_history = {}

    def _check_repetitive_command(self, cmd_text: str):
        import time
        now = time.time()
        # Clean up old entries
        self._command_history = {k: [t for t in v if now - t < 60] for k, v in self._command_history.items()}
        history = self._command_history.get(cmd_text, [])
        if len(history) >= 2:
            raise ValueError("1 分钟内相同指令最多发送 2 次，请稍后再试。")

    async def _inject_narrative_brief(self, req: CommandRequest, cmd_text: str) -> str:
        """阶段3集成B:按 options.chapter_number 注入结构层生成简报(失败静默降级)。"""
        raw_ch = None
        try:
            raw_ch = req.options.get("chapter_number") if req.options else None
        except Exception:
            raw_ch = None
        if not raw_ch or self._db is None or not req.project_id:
            return cmd_text
        try:
            chapter_number = int(raw_ch)
        except (TypeError, ValueError):
            return cmd_text
        try:
            from services.narrative_structure import NarrativeStructureService

            nsvc = NarrativeStructureService(self._db)
            await nsvc.initialize()
            brief = await nsvc.render_generation_brief(req.project_id, chapter_number)
            if brief:
                logger.info(
                    "[GlobalRouter] 已注入叙事结构层生成简报(第%d章, +%d 字符)",
                    chapter_number, len(brief),
                )
                return brief + cmd_text
        except Exception as exc:
            logger.warning("[GlobalRouter] 生成简报注入失败(降级原始指令): %s", exc)
        return cmd_text
        history.append(now)
        self._command_history[cmd_text] = history

    def _check_vram_limit(self, cmd_text: str, mode: str):
        # 显存预估器：根据模型参数量 + 输入长度估算显存占用
        # 假设基础模型占用 8000 MB, 每个 token 约占 0.5 MB 显存上下文
        # 6900XT 16GB * 85% = 13926 MB
        estimated_token_count = len(cmd_text)
        if estimated_token_count > 8000:
            raise ValueError(f"指令 Token 上限校验失败：指令长度超过 8000 token（当前估算 {estimated_token_count} token），超长直接拒绝。")
        
        # 简单预估模型: 参数 8000MB + token * 0.5MB
        vram_estimate_mb = 8000 + (estimated_token_count * 0.5)
        if mode in ("think", "deep"):
            vram_estimate_mb += 2000 # 思考模式额外开销
            
        max_vram_mb = 16384 * 0.85 # 13926.4 MB
        if vram_estimate_mb > max_vram_mb:
            raise ValueError("显存预估超过 6900XT 可用显存的 85%，拒绝入队。请降级模型模式或对任务进行拆分。")

    # ── 批次1 引擎集成辅助 ──────────────────────────────────────

    def _use_batch1_engine(self) -> bool:
        """是否启用批次1引擎（配置开关 + 引擎已挂载双条件）。"""
        from core.config_manager import config_manager

        enabled = bool(
            config_manager.get("global_router.use_batch1_engine", True)
        )
        return enabled and self.batch1_task_manager is not None

    @staticmethod
    def _is_long_task(
        cmd_text: str, intent: str, mode: str, use_split: bool
    ) -> bool:
        """
        长任务识别：创作 / 书籍量化 / 深度思考 / 强制分段 / 超长文本。
        命中任一条件即判定为长任务，走批次1分段引擎。
        """
        from core.config_manager import config_manager

        if use_split:
            return True
        if str(mode).strip().lower() in _DEEP_THINK_MODES:
            return True
        threshold = int(
            config_manager.get(
                "task.long_command_min_chars", DEFAULT_LONG_COMMAND_MIN_CHARS
            )
        )
        if len(cmd_text) >= threshold:
            return True
        return False

    @staticmethod
    def _map_mode_to_model_source(mode: str) -> str:
        """批次5算力模式 → 批次1 model_source 标记。"""
        mode_lower = str(mode).strip().lower()
        if mode_lower in ("think", "deep", "reasoning", "deepseek-r1"):
            return "cloud"
        return "local"

    @staticmethod
    def _resolve_segment_strategy(
        req: CommandRequest, default_strategy: str | None
    ) -> str | None:
        """
        B1-08：API 入参透传 segment_strategy（允许前端手动传
        force_split / no_split / auto 做调试）。
        非法值忽略；default_strategy 为 None 时返回 None 表示无透传。
        """
        opt = req.options.get("segment_strategy")
        if opt is not None:
            norm = normalize_segment_strategy(opt)
            if str(opt).strip().lower() == norm and norm in (
                SEGMENT_STRATEGY_FORCE_SPLIT,
                SEGMENT_STRATEGY_NO_SPLIT,
                SEGMENT_STRATEGY_AUTO,
            ):
                return norm
        return default_strategy

    @staticmethod
    def _extract_book_id(cmd_text: str) -> str | None:
        """从指令文本解析 book_id（兼容 'quantize book xxx' 等格式）。"""
        import re

        m = re.search(r"\b(?:book|书籍|书)\s+([A-Za-z0-9_\-]+)", cmd_text)
        return m.group(1) if m else None

    async def _recommend_strategy(
        self,
        req: CommandRequest,
        cmd_text: str,
        hint: str | None = None,
    ) -> str:
        """
        B1-09：调用 LoadEstimator 预估推荐 segment_strategy。

        优先级：options 透传（用户显式）> hint（业务显式）> LoadEstimator 自动预估 > auto。
        预估器未挂载 / 开关关闭时，返回 hint 或 auto（回退补丁前行为）。
        """
        # 用户显式透传优先级最高（调试用）
        passthrough = self._resolve_segment_strategy(req, None)
        if passthrough is not None:
            return passthrough

        if self.load_estimator is None:
            return hint or SEGMENT_STRATEGY_AUTO

        book_id = req.options.get("book_id") or self._extract_book_id(cmd_text)
        try:
            return await self.load_estimator.estimate(
                raw_command=cmd_text,
                project_id=req.project_id,
                book_id=book_id,
                hint_force=hint,
            )
        except Exception as exc:
            # 预估异常降级：绝不阻断任务提交
            logger.warning(
                "[LOAD_EST] 负载预估调用异常，降级策略=%s: %s",
                hint or SEGMENT_STRATEGY_AUTO, exc,
            )
            return hint or SEGMENT_STRATEGY_AUTO

    async def _submit_to_batch1(
        self,
        cmd_text: str,
        priority_level: PriorityLevel,
        segment_strategy: str = "auto",
        mode: str = "rapid",
        idempotency_key: str | None = None,
        project_id: str | None = None,
    ) -> CommandTask:
        """构造标准 CommandTask 并提交批次1引擎。

        project_id（阶段1）：创作任务的项目绑定，注入首段尾巴随接力传递，
        使执行钩子经 dispatcher 加载项目绑定书库的卡片上下文。
        """
        priority = _PRIORITY_LEVEL_TO_BATCH1.get(priority_level, 4)
        task = await self.batch1_task_manager.submit_task(
            raw_command=cmd_text,
            priority=priority,
            segment_strategy=segment_strategy,
            model_source=self._map_mode_to_model_source(mode),
            idempotency_key=idempotency_key,
            project_id=project_id,
        )
        logger.info(
            "[GlobalRouter] 已提交批次1引擎: task=%s, 分段数=%d, 优先级=%d",
            task.task_id, len(task.segments), priority,
        )
        return task
        
    # ── 意图分发（route_command 按意图拆分后的独立处理器） ──────

    async def _route_project_management(self, req: CommandRequest) -> dict[str, Any]:
        """批次3 同步API链路处理，直接放行交由具体 handler 执行。"""
        return {"status": "bypassed", "intent": "PROJECT_MANAGEMENT", "target": "Batch3_ProjectManager"}

    async def _route_library(self, req: CommandRequest, command) -> dict[str, Any]:
        """Execute read-only retrieval or explicit author card operations."""
        if command.action == "retrieve_material":
            result = LibraryCatalog().retrieve(command.query, limit=int(command.filters.get("limit", 8)))
            return {
                "status": "completed", "intent": "LIBRARY", "action": command.action,
                "query": command.query, "hits": [hit.model_dump(mode="json") for hit in result.hits],
                "trace": result.trace,
            }
        if command.action == "audit":
            catalog = LibraryCatalog()
            manifest = catalog.manifest()
            report = catalog.audit()
            return {
                "status": "completed", "intent": "LIBRARY", "action": command.action,
                "report": report.model_dump(mode="json") | {"has_duplicate_ids": report.has_duplicate_ids},
                "trace": [{"stage": "manifest_audit", "root": manifest.root_source_document_id}],
            }
        if self.indexer is None:
            return {"status": "blocked", "intent": "LIBRARY", "reason": "INDEXER_UNAVAILABLE"}
        if command.action == "retrieve_card":
            cards = await self.indexer.search_cards(keyword=command.query or None, **command.filters)
            return {"status": "completed", "intent": "LIBRARY", "action": command.action, "cards": cards,
                    "trace": [{"stage": "card_index", "count": len(cards), "filters": command.filters}]}
        if command.action == "create_card":
            payload = dict(command.payload)
            payload.setdefault("source_book_id", req.project_id or "author_library")
            payload.setdefault("content", command.query)
            if payload.get("card_type", "info") == "data":
                payload.setdefault("metric_type", "author_metric")
                payload.setdefault("value", {})
                card = DataCard(**payload)
            else:
                payload.setdefault("card_sub_type", payload.get("knowledge_type", "author_note"))
                card = InfoCard(**payload)
            card_id = await self.indexer.save_card(card)
            return {"status": "completed", "intent": "LIBRARY", "action": command.action, "card_id": card_id,
                    "trace": [{"stage": "card_write", "card_id": card_id}]}
        if command.action == "maintain_card":
            if not command.card_id:
                raise ValueError("维护卡片必须提供 card_id")
            updated = await self.indexer.update_card_metadata(command.card_id, **command.payload)
            return {"status": "completed" if updated else "not_found", "intent": "LIBRARY", "action": command.action,
                    "card_id": command.card_id, "trace": [{"stage": "card_update", "updated": updated}]}
        if command.action == "link_cards":
            if not command.card_id or not command.target_card_id:
                raise ValueError("关联卡片必须提供 card_id 与 target_card_id")
            relation_id = await self.indexer.upsert_relation(
                command.card_id, command.target_card_id, command.relation_type,
                float(command.payload.get("weight", 1.0)), str(command.payload.get("note", "")),
            )
            return {"status": "completed", "intent": "LIBRARY", "action": command.action,
                    "relation_id": relation_id, "trace": [{"stage": "relation_write", "relation_id": relation_id}]}
        raise ValueError(f"不支持的书库命令: {command.action}")

    async def _route_reflection(self, req: CommandRequest) -> dict[str, Any]:
        """批次4 的重度离线分析，直接下发到底层触发器。"""
        session_id = await self.reflection_trigger.trigger(trigger_type="MANUAL")
        return {"status": "dispatched", "intent": "REFLECTION", "session_id": session_id}

    async def _route_quantization(
        self, req: CommandRequest, cmd_text: str
    ) -> dict[str, Any]:
        """批次2：知识库构建长任务（批次1引擎 or 旧闭包队列回退）。"""
        # 【Phase 9】获取量化霸权锁
        self.system_monitor.acquire_quantization_lock()

        strategy = self._resolve_segment_strategy(req, SEGMENT_STRATEGY_FORCE_SPLIT)
        if self._use_batch1_engine():
            task = await self._submit_to_batch1(
                cmd_text, PriorityLevel.LV6, segment_strategy=strategy,
                mode="rapid",
            )

            # 启动后台守护任务，监控量化完成并释放锁
            async def _watch_quantization():
                try:
                    # 轮询等待（简化处理，实际可替换为 webhook 或 callback）
                    terminal_statuses = {"COMPLETED", "FAILED", "CANCELLED"}
                    while True:
                        status = await self.batch1_task_manager.get_task_status(task.task_id)
                        if status is None:
                            # 任务记录已不存在，终止轮询
                            break
                        # get_task_status 返回 TaskStatusResponse 对象，
                        # 必须取出 status 的 value 再与字符串比较，否则恒为 False 导致锁永不释放
                        current = getattr(status.status, "value", str(status.status))
                        if current in terminal_statuses:
                            break
                        await asyncio.sleep(5)
                finally:
                    self.system_monitor.release_quantization_lock()
                    logger.info("[GlobalRouter] 量化锁守护协程退出，锁已释放")
            asyncio.create_task(_watch_quantization())

            return {
                "status": "queued",
                "intent": "QUANTIZATION",
                "priority": PriorityLevel.LV6.name,
                "task_id": task.task_id,
                "engine": "batch1",
                "segment_count": len(task.segments),
                "segment_strategy": strategy,
            }

        # ── 旧逻辑（兼容回退）：内存中枢门面缓冲排队 ──
        task_id = f"quant_{uuid.uuid4().hex[:8]}"
        async def _lazy_quant():
            logger.info("[GlobalRouter] 准备正式移交量化持久化任务: %s", task_id)
            if self._use_batch1_engine():
                await self._submit_to_batch1(
                    cmd_text, PriorityLevel.LV6, segment_strategy=strategy,
                    mode="rapid",
                )
                return
            await asyncio.sleep(0.5)

        await self.task_queue.push(task_id, _lazy_quant, PriorityLevel.LV6)
        return {"status": "queued", "intent": "QUANTIZATION", "priority": PriorityLevel.LV6.name, "task_id": task_id}

    async def _route_doc_learning(
        self, req: CommandRequest, cmd_text: str
    ) -> dict[str, Any]:
        """文档学习意图：短指令 + 后台大文档解析重负载。"""
        strategy = self._resolve_segment_strategy(req, SEGMENT_STRATEGY_FORCE_SPLIT)
        if self._use_batch1_engine():
            task = await self._submit_to_batch1(
                cmd_text, PriorityLevel.LV6, segment_strategy=strategy,
                mode="rapid",
            )
            return {
                "status": "queued",
                "intent": "DOC_LEARNING",
                "priority": PriorityLevel.LV6.name,
                "task_id": task.task_id,
                "engine": "batch1",
                "segment_count": len(task.segments),
                "segment_strategy": strategy,
            }

        # ── 兼容回退：旧队列闭包占位 ──
        task_id = f"doclearn_{uuid.uuid4().hex[:8]}"
        async def _lazy_doclearn():
            logger.info("[GlobalRouter] 文档学习任务排队等待移交: %s", task_id)
            if self._use_batch1_engine():
                await self._submit_to_batch1(
                    cmd_text, PriorityLevel.LV6, segment_strategy=strategy,
                    mode="rapid",
                )
                return
            await asyncio.sleep(0.5)

        await self.task_queue.push(task_id, _lazy_doclearn, PriorityLevel.LV6)
        return {"status": "queued", "intent": "DOC_LEARNING", "priority": PriorityLevel.LV6.name, "task_id": task_id}

    async def _route_creation(
        self, req: CommandRequest, cmd_text: str
    ) -> dict[str, Any]:
        """批次3最复杂的创作指令：规则匹配、负载判定、批次1/发散引擎/旧队列三路分发。"""
        # 【Phase 9】量化霸权锁检查
        if self.system_monitor.is_locked_for_quantization():
            logger.warning("[GlobalRouter] 创作请求被拦截：系统正在全力进行书库量化")
            return {
                "status": "blocked",
                "intent": "CREATION",
                "reason": "QUANTIZATION_LOCK_ACTIVE",
                "message": "总督提示：当前算力已全部倾斜至书库量化，为保证速度，AI 创作通道暂时挂起。您可以继续在画板进行手工码字。"
            }

        # 阶段3集成B(讨论稿第七章):命令携带 chapter_number 时,注入叙事结构层
        # 生成简报(已确认拍纲+节奏软目标+伏笔硬约束)——结构资产进入创作链路。
        # 任何失败仅告警降级,不影响原有创作路径。
        cmd_text = await self._inject_narrative_brief(req, cmd_text)

        # a) 先获取批次4反馈沉淀的动态优化规则
        context_features = {
            "command_type": "CREATION",
            "command_text": cmd_text,
            "estimated_length": len(cmd_text),
            "project_id": req.project_id,
        }
        rules = await self.optimization_applier.fetch_active_rules(
            scope=None,
            context_features=context_features
        )
        mode = req.options.get("mode", "think")
        use_split = False

        # b) 判断是否有规则要求强制降级或分段
        for rule in rules:
            if rule.scope == "MODEL_DISPATCH" and rule.action.get("override_compute_mode"):
                mode = rule.action.get("override_compute_mode")
                logger.info("[GlobalRouter] 触发动态降级规则，强制算力切换为: %s", mode)
            if rule.scope == "TASK_SPLIT" and rule.action.get("force_split_chunks"):
                use_split = True
                logger.info("[GlobalRouter] 触发分段保护规则，启动内容降维拆分")

        # c) 检查系统硬负载情况（若内存吃紧，同样强制降级 rapid）
        health = self.system_monitor.get_health_report()
        if health.get("resource", {}).get("memory_percent", 0) > 85.0:
            logger.warning("[GlobalRouter] 系统内存负载大于85%，强制回退 rapid 模型以防止崩溃。")
            mode = "rapid"

        # d) 长任务判定（B1-09 增强：LoadEstimator 四类场景自动匹配策略）
        is_long = self._is_long_task(cmd_text, "CREATION", mode, use_split)
        deep_mode = str(mode).strip().lower() in _DEEP_THINK_MODES
        hint = (
            SEGMENT_STRATEGY_FORCE_SPLIT
            if (use_split or deep_mode)
            else None
        )
        strategy = await self._recommend_strategy(req, cmd_text, hint=hint)
        if strategy == SEGMENT_STRATEGY_FORCE_SPLIT and not is_long:
            logger.info(
                "[LOAD_EST] 预估推荐 force_split，短指令高负载判定为长任务"
            )
            is_long = True

        if is_long:
            p_level = PriorityLevel.LV6 if use_split else PriorityLevel.LV4
            # Phase 13: 繁杂和思考模式，强制走发散引擎（如果已装配）
            use_divergent = mode in ("complex", "think", "deep") and self.divergent_engine is not None

            if self._use_batch1_engine() and not use_divergent:
                task = await self._submit_to_batch1(
                    cmd_text,
                    p_level,
                    segment_strategy=strategy,
                    mode=mode,
                    idempotency_key=req.options.get("idempotency_key"),
                    project_id=req.project_id,
                )
                logger.info(
                    "[GlobalRouter] 长任务判定生效，改走批次1引擎: "
                    "task=%s, 分段数=%d, 算力=%s, split=%s, 策略=%s",
                    task.task_id, len(task.segments), mode, use_split, strategy,
                )
                return {
                    "status": "queued",
                    "intent": "CREATION",
                    "priority": p_level.name,
                    "task_id": task.task_id,
                    "engine": "batch1",
                    "segment_count": len(task.segments),
                    "segment_strategy": strategy,
                    "decision": {"compute_mode": mode, "split": use_split}
                }
            if use_divergent:
                logger.info("[GlobalRouter] 命中发散模式，拦截批次1长任务管线，直接提交发散引擎。")
            else:
                logger.warning(
                    "[GlobalRouter] 长任务判定生效但批次1引擎未挂载/已关闭，"
                    "回退旧闭包队列执行"
                )

        task_id = f"create_{uuid.uuid4().hex[:8]}"
        async def _lazy_creation():
            logger.info("[GlobalRouter] 将创作请求移交底层持久化管线: %s，配置[%s, split=%s]", task_id, mode, use_split)
            if self.model_dispatcher:
                from api.websocket import manager as ws_manager
                from services.session_pool import session_pool
                try:
                    # ══════════════════════════════════════════════════════
                    # 第十部分：多智能体小说创作优先（叠加式）
                    # 总开关 feature.novel_multi_agent_enable 开启且 NovelSupervisor
                    # 已装配时，先走多智能体执行（含规则/技能回注、审计采集），
                    # 生成结果作为本次创作输出；否则完全回退原链路。
                    # ══════════════════════════════════════════════════════
                    result = None
                    if self.novel_supervisor is not None and self.novel_supervisor.is_enabled():
                        try:
                            nv_result = await self.novel_supervisor.execute_creation(
                                task_id=task_id,
                                cmd_text=cmd_text,
                                project_id=req.project_id or "",
                                genre=req.options.get("genre", "") or "",
                                scenario=req.options.get("scenario", "") or "",
                            )
                            if nv_result.get("success"):
                                result = nv_result.get("result") or cmd_text
                                # 创作完成后按样本量触发低优先级自学习反思（不抢占算力）
                                if (
                                    self.novel_supervisor.is_self_reflect_learn_enabled()
                                    and hasattr(self, "_novel_learning_loop")
                                    and self._novel_learning_loop is not None
                                ):
                                    try:
                                        await self._novel_learning_loop.submit_reflection_task()
                                    except Exception as learn_exc:
                                        logger.warning("[GlobalRouter] 多智能体反思任务投递失败: %s", learn_exc)
                            else:
                                logger.warning("[GlobalRouter] 多智能体执行未成功，回退原链路: %s", nv_result.get("error", "unknown"))
                        except Exception as nv_exc:
                            logger.warning("[GlobalRouter] 多智能体执行异常，回退原链路: %s", nv_exc)
                    if result is None:
                        # 多智能体未产出结果时，完全回退原链路
                        if mode == "complex" and self.divergent_engine is not None:
                            result = await self.divergent_engine.execute_complex_mode(cmd_text, req.project_id)
                        elif mode in ("think", "deep") and self.divergent_engine is not None:
                            result = await self.divergent_engine.execute_think_mode(cmd_text, req.project_id)
                        else:
                            result = await self.model_dispatcher.dispatch(cmd_text, req.project_id, override_mode=mode)

                    # 持久化逻辑
                    sess_id = req.session_id or f"temp_{uuid.uuid4().hex[:8]}"
                    if session_pool.get_session(sess_id) is None:
                        session_pool.create_session(model_key="default", bind_project=req.project_id)
                        # override created session_id to align with frontend session
                        sess = session_pool._memory_cache[list(session_pool._memory_cache.keys())[-1]]
                        sess.session_id = sess_id
                        session_pool._save_to_disk(sess)
                        session_pool._memory_cache[sess_id] = session_pool._memory_cache.pop(list(session_pool._memory_cache.keys())[-1])

                    session_pool.append_message(sess_id, "user", cmd_text)
                    session_pool.append_message(sess_id, "assistant", result)

                    # 单独持久化到 chat_history 以防膨胀
                    try:
                        if self.project_manager and self.project_manager.db:
                            await self.project_manager.db.insert_chat_history(
                                task_id=task_id,
                                session_id=sess_id,
                                project_id=req.project_id,
                                user_query=cmd_text,
                                ai_result=result
                            )
                    except Exception as db_err:
                        logger.error("[GlobalRouter] 持久化对话记录失败: %s", db_err)

                    if req.options.get("insert_to_doc") and req.project_id and self.project_manager:
                        from models.project import ProjectDoc
                        new_doc = ProjectDoc(
                            project_id=req.project_id,
                            doc_name=f"生成片段-{task_id}",
                            raw_content=result
                        )
                        await self.project_manager.add_document(new_doc)

                    await ws_manager.send_to_session(req.session_id or sess_id, result)

                    # Auto-reflection sampling or Think mode scoring
                    from core.config_manager import config_manager
                    from services.scoring_engine import scoring_engine
                    import random

                    # 【Phase 9】 如果是 think (思考模式)，必定触发"生成后打分"闭环
                    if mode.strip().lower() in _DEEP_THINK_MODES:
                        logger.info("[GlobalRouter] 思考模式开启：文章已生成，交由打分引擎进行交叉审查...")
                        async def _run_scoring():
                            await scoring_engine.score_generation(
                                project_id=req.project_id,
                                command_text=cmd_text,
                                generated_content=result,
                                used_cards=[] # TODO: 从调度器透传提取的卡片
                            )
                        asyncio.create_task(_run_scoring())

                    # 随机 Auto-reflection
                    elif config_manager.get("auto_reflection_after_creation.enable", False):
                        prob = config_manager.get("auto_reflection_after_creation.probability", 0.1)
                        threshold = config_manager.get("auto_reflection_after_creation.min_token_threshold", 500)
                        if random.random() < prob or len(result) > threshold:
                            logger.info("[GlobalRouter] 达到采样条件，触发后台轻量级自动反思...")
                            asyncio.create_task(self.reflection_trigger.trigger(trigger_type="AUTO"))

                except Exception as e:
                    logger.error("模型调度发生异常: %s", e)
                    await ws_manager.send_to_session(req.session_id or sess_id, f"【系统故障】大模型调用失败: {e}")
            else:
                await asyncio.sleep(0.5)

        # 根据是否强制拆分调整优先级
        p_level = PriorityLevel.LV6 if use_split else PriorityLevel.LV4
        await self.task_queue.push(task_id, _lazy_creation, p_level)

        return {
            "status": "queued",
            "intent": "CREATION",
            "priority": p_level.name,
            "task_id": task_id,
            "decision": {"compute_mode": mode, "split": use_split}
        }

    async def route_command(self, req: CommandRequest) -> dict[str, Any]:
        """意图分发入口：校验项目 → 识别意图 → 分发到独立处理器。"""
        if req.project_id and self.project_manager:
            exists = await self.project_manager.exists(req.project_id)
            if not exists:
                raise ValueError(f"指定的项目不存在: {req.project_id}")

        cmd_text = req.command.strip()
        logger.info("[GlobalRouter] 收到原始指令: %s", cmd_text)

        # 前置拦截
        self._check_repetitive_command(cmd_text)
        mode = req.options.get("mode", "rapid")
        self._check_vram_limit(cmd_text, mode)

        # 1. 意图提取
        from core.config_manager import config_manager

        intent = "CREATION"
        enable_raw = config_manager.get("enable_raw_keyword_intent", True)
        is_cmd = req.options.get("is_command_mode", False)

        if enable_raw and is_cmd:
            detector = KeywordIntentDetector()
            intent = detector.detect(cmd_text)

        library_command = parse_library_command(cmd_text, req.options)
        if library_command is not None:
            intent = "LIBRARY"

        # 2. 路由分发（按意图拆分的独立处理器）
        handlers = {
            "LIBRARY": lambda: self._route_library(req, library_command),
            "PROJECT_MANAGEMENT": lambda: self._route_project_management(req),
            "REFLECTION": lambda: self._route_reflection(req),
            "QUANTIZATION": lambda: self._route_quantization(req, cmd_text),
            "DOC_LEARNING": lambda: self._route_doc_learning(req, cmd_text),
            "CREATION": lambda: self._route_creation(req, cmd_text),
        }
        return await handlers[intent]()