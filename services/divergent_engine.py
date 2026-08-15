"""
services/divergent_engine.py — 发散思维核心引擎 (Phase 13)
=========================================================
实现“繁杂模式(Complex Mode)”和新版“思考模式(Think Mode)”的发散逻辑。
依靠本地硬盘的 temp_manager 和大量算力，模拟人类网文作家的头脑风暴，
最后将经验反思沉淀进书库。
"""
import os
import uuid
import logging
import asyncio
from pathlib import Path
from core.temp_manager import temp_manager
from services.dispatcher import ModelDispatcher
from services.indexer import CardIndexer
from models.cards import InfoCard  # 修复：原错误导入 models.card.Card/InfoCardData（模块不存在）

logger = logging.getLogger(__name__)

class DivergentEngine:
    def __init__(self, dispatcher: ModelDispatcher, indexer: CardIndexer):
        self.dispatcher = dispatcher
        self.indexer = indexer
        # P2-2.4 修复：登记本次执行创建的临时工作区，任务结束统一回收
        self._active_workspaces: set[Path] = set()

    async def _save_temp_file(self, content: str, prefix: str) -> Path:
        """保存中间推演到局部临时文件，并广播到潜意识流悬浮窗。

        P2-2.4 修复：workspace 登记到实例级 _active_workspaces，
        由 execute_* 在任务结束时统一 cleanup_workspace，杜绝长期运行无界累积。
        """
        workspace = temp_manager.create_workspace(function_type="divergence")
        self._active_workspaces.add(workspace)
        temp_file = workspace / f"{prefix}_{uuid.uuid4().hex[:8]}.txt"
        
        # 异步文件写入
        await asyncio.to_thread(temp_file.write_text, content, encoding="utf-8")
        
        # 广播潜意识流 WebSocket 事件
        from api.websocket import manager
        await manager.broadcast({
            "type": "subconscious_stream",
            "file": temp_file.name,
            "content": content[-5000:] if len(content) > 5000 else content
        })
        
        return temp_file

    def _cleanup_active_workspaces(self) -> None:
        """任务结束：精准销毁本次执行创建的全部临时工作区（幂等）。"""
        while self._active_workspaces:
            workspace = self._active_workspaces.pop()
            try:
                # 先解除运行引用，再执行显式的任务结束清理；GC 仍受 marker 保护。
                active_marker = workspace / ".active"
                active_marker.unlink(missing_ok=True)
                temp_manager.cleanup_workspace(workspace)
            except Exception as exc:  # pragma: no cover
                logger.warning("[DivergentEngine] 工作区清理失败: %s - %s", workspace, exc)

    async def execute_complex_mode(self, cmd_text: str, project_id: str | None = None) -> str:
        """
        执行繁杂模式。
        流程：
        1. 发散推演（头脑风暴），找出需要的卡片方向。
        2. 极大数据量索引提取。
        3. 融合输出正文。
        """
        logger.info("[DivergentEngine] 启动繁杂模式 (Complex Mode)！")
        try:
            return await self._execute_complex_mode_inner(cmd_text, project_id)
        finally:
            self._cleanup_active_workspaces()

    async def _execute_complex_mode_inner(self, cmd_text: str, project_id: str | None = None) -> str:
        # 1. 深度发散推演
        # 架构整改 1.3：prompt 迁移模板；模板缺失回退旧基线硬编码。
        from services.prompt_template_manager import prompt_manager
        from services.legacy_prompts import divergent_brainstorm

        brainstorm_prompt = prompt_manager.render_or_fallback(
            "divergent_brainstorm",
            {"cmd_text": cmd_text},
            lambda: divergent_brainstorm(cmd_text),
        )
        # 用 think 模式的算力参数来跑发散
        # 代码组织优化：走调度器公开接口 call_cloud（含密钥门控），
        # 不再直接访问 _call_cloud_model 私有方法。
        brainstorm_result = await self.dispatcher.call_cloud(brainstorm_prompt, temperature=0.8, max_tokens=2048)
        
        # 写入临时文件防 OOM
        await self._save_temp_file(brainstorm_result, "brainstorm")

        # 2. 深度检索卡片
        # 取决于上下文极限
        context_limit = self.dispatcher.get_context_limit()
        logger.info(f"[DivergentEngine] 繁杂模式触发深网检索，允许拉取的 Token 极限: {context_limit}")
        
        # 为了演示，直接把 brainstorm 结果交给 indexer 里的矢量搜索或者简单的字数堆叠。
        # 这里模拟调用大量卡片（不再局限于 LIMIT 20，而是用 context_limit 控制）
        limit_count = max(20, context_limit // 400) # 假设单张卡片平均 400 token，最多可能拉上百张
        
        # 我们根据 project 找书库
        bind_book_ids = []
        if project_id and self.dispatcher.project_manager:
            proj = await self.dispatcher.project_manager.get_project(project_id)
            if proj:
                bind_book_ids = proj.bind_book_ids
        
        contexts = []
        if bind_book_ids:
            for book_id in bind_book_ids:
                # 把逻辑发散类型的卡片也拿出来，利用曾经的经验！
                # 代码组织优化：走 indexer 公开接口 get_card_summaries，
                # 不再直接拼 SQL 访问 indexer.conn。
                logic_summaries = await self.indexer.get_card_summaries(
                    source_book=book_id,
                    limit=5,
                    subtype_like="%logic_%",
                )
                if logic_summaries:
                    contexts.append("【前人留下的发散思维经验结晶】")
                    contexts.extend(logic_summaries)

                summaries = await self.indexer.get_card_summaries(
                    source_book=book_id,
                    limit=limit_count,
                )
                contexts.extend(summaries)
                
        massive_context = "\n".join(contexts)
        # 写入临时文件
        await self._save_temp_file(massive_context, "massive_context")

        # 3. 终极融合输出
        fusion_prompt = (
            f"【发散思考日志】\n{brainstorm_result}\n\n"
            f"【海量设定与数据参考】\n{massive_context[:context_limit]}\n\n"
            f"你现在拥有海量的世界观设定和刚才缜密的发散思考。\n"
            f"请根据以上所有信息，正式执行作者最初的指令，生成极高质量的小说正文/情节推演：\n"
            f"【原始指令】{cmd_text}\n"
        )
        
        logger.info("[DivergentEngine] 繁杂模式完成检索，开始最终降维打击生成...")
        final_result = await self.dispatcher.call_cloud(fusion_prompt, temperature=0.9, max_tokens=4000)
        
        return final_result

    async def execute_think_mode(self, cmd_text: str, project_id: str | None = None) -> str:
        """
        执行深度思考模式。
        流程：
        1. 发散提问，寻找痛点。
        2. 生成经验，存入书库。
        3. 回报作者。
        """
        logger.info("[DivergentEngine] 启动思考模式 (Think Mode) 发散！")
        try:
            # 架构整改 1.3：prompt 迁移模板；模板缺失回退旧基线硬编码。
            from services.prompt_template_manager import prompt_manager
            from services.legacy_prompts import divergent_think

            think_prompt = prompt_manager.render_or_fallback(
                "divergent_think",
                {"cmd_text": cmd_text},
                lambda: divergent_think(cmd_text),
            )
            think_result = await self.dispatcher.call_cloud(think_prompt, temperature=0.8, max_tokens=2048)
            
            # 沉淀结晶：向书库压入多维经验卡片
            if project_id and self.dispatcher.project_manager:
                proj = await self.dispatcher.project_manager.get_project(project_id)
                if proj and proj.bind_book_ids:
                    book_id = proj.bind_book_ids[0]
                    # 修复：对齐 models/cards.py 的 InfoCard 模型与 CardIndexer.save_card 接口
                    new_card = InfoCard(
                        source_book_id=book_id,
                        content=f"针对指令【{cmd_text[:10]}...】的发散经验：\n{think_result}",
                        tags=["发散思维", "AI结晶"],
                        card_sub_type="logic_general_divergence",
                        category="misc",
                        original_fragment="",
                        payload={"source": "divergent_engine_think_mode"}
                    )
                    await self.indexer.save_card(new_card)
                    logger.info(f"[DivergentEngine] 结晶已沉淀入书库: {book_id}")

            return f"【深度思考报告】\n{think_result}\n\n(提示：本次发散思考的结晶已被量化并作为经验卡片存入系统，未来的繁杂模式写作会自动调用此经验。)"
        finally:
            self._cleanup_active_workspaces()
