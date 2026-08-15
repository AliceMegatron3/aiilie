"""
services/load_estimator.py — 指令负载预估器（B1-09 新建）
=========================================================
区分 4 类业务场景，自动匹配最合适的 segment_strategy，
减少不必要拆分带来的算力浪费，同时保证重负载任务一定启用
多节点分段、尾巴接力、断点恢复能力。

四类场景映射：
  短指令-短内容（SHORT+LOW）    → no_split     单节点执行，不拆分，节省算力
  短指令-长内容（SHORT+HIGH）   → force_split  强制多节点分段（修复"字符数不足未分段"）
  长指令-长内容（LONG+HIGH）    → force_split  多节点分段执行
  长指令-短内容（LONG+LOW）     → auto         按原有字符阈值轻量拆分，避免过度拆分

设计要点：
1. **只统计元数据/计数**：外部资源维度仅做 SQL COUNT(*) 与文件 stat()，
   绝不加载完整卡片 .card 正文 / 文档 .md 正文，预估阶段算力开销压到最低；
2. **优先级分层**：hint_force（上层业务显式 hint）> 自动预估 > 默认 auto；
3. **异常降级**：任何外部查询异常直接回退策略 auto，绝不阻断任务提交；
4. **一键开关**：config.yaml load_estimator.enable=false 时整体关闭，
   全部任务回到原始 auto 逻辑，行为与补丁前完全一致。
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir
from models.task import normalize_segment_strategy

logger = logging.getLogger(__name__)

# 推荐策略字面量
RecommendStrategy = Literal["auto", "force_split", "no_split"]

# 外部负载等级
EXTERNAL_LOW = "EXTERNAL_LOW"
EXTERNAL_HIGH = "EXTERNAL_HIGH"

# 指令长度等级
COMMAND_SHORT = "SHORT"
COMMAND_LONG = "LONG"

# 默认阈值（可被 config.yaml load_estimator 覆盖）
DEFAULT_COMMAND_SHORT_THRESHOLD = 1200   # 指令字符 ≤ 1200 视为 SHORT
DEFAULT_EXTERNAL_HIGH_CARD_THRESHOLD = 8  # 卡片数 ≥ 8 视为 EXTERNAL_HIGH
# 模块内固定阈值：文档数 ≥ 3 / 存在 AI 解析成果 / 书籍文件 ≥ 200KB 视为高负载
DEFAULT_EXTERNAL_HIGH_DOC_THRESHOLD = 3
DEFAULT_LONG_BOOK_SIZE_BYTES = 200 * 1024


class LoadEstimator:
    """
    指令负载预估器。

    依赖注入（均为可选，未注入时对应维度按最低负载处理）：
      - indexer: CardIndexer —— 卡片元数据计数（library_index.db）
      - project_manager: ProjectManager —— 项目文档/解析成果计数
    """

    def __init__(
        self,
        indexer: Any | None = None,
        project_manager: Any | None = None,
    ) -> None:
        self._indexer = indexer
        self._project_manager = project_manager
        # 书库原始文件目录（与 BookQuantifier 同源）
        self._books_dir = get_app_data_dir().parent / "library" / "books"
        logger.info(
            "负载预估器初始化: indexer=%s, project_manager=%s",
            "已挂载" if indexer else "未挂载",
            "已挂载" if project_manager else "未挂载",
        )

    # ── 阈值读取（config 优先） ─────────────────────────────────

    def _command_short_threshold(self) -> int:
        return int(
            config_manager.get(
                "load_estimator.command_short_threshold",
                DEFAULT_COMMAND_SHORT_THRESHOLD,
            )
        )

    # ── 专项2修复：token 预算估算与校验 ──────────────────────────

    def _token_budget(self) -> int:
        """读取全局单任务 token 预算（来自 config，默认 8192）。"""
        return int(
            config_manager.get("load_estimator.token_budget", 8192)
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """轻量 token 估算：中文字符按 ~1.5 chars/token，其余按 4 chars/token。

        不引入额外 tokenizer 依赖，仅做标尺用途（预算校验是启发式保护，
        非精确计费）；估算值仅供 load_estimator 判定与 splitter 切割约束。
        """
        if not text:
            return 0
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other = len(text) - cjk
        return int(cjk / 1.5 + other / 4.0 + 0.5)

    def check_token_budget(self, raw_command: str, ext_text: str = "") -> tuple[bool, int, int]:
        """
        校验单任务 token 预算（专项2新增）。

        Returns:
            (over_budget, estimated_tokens, budget)
            超过预算返回 (True, est, budget)，供上层强制 force_split；
            未超返回 (False, est, budget)。
        """
        budget = self._token_budget()
        est = self._estimate_tokens((raw_command or "") + "\n" + (ext_text or ""))
        return est > budget, est, budget

    def _external_high_card_threshold(self) -> int:
        return int(
            config_manager.get(
                "load_estimator.external_high_card_threshold",
                DEFAULT_EXTERNAL_HIGH_CARD_THRESHOLD,
            )
        )

    # ── 主入口 ──────────────────────────────────────────────────

    async def estimate(
        self,
        raw_command: str,
        project_id: str | None = None,
        book_id: str | None = None,
        hint_force: str | None = None,
    ) -> RecommendStrategy:
        """
        预估任务负载并输出推荐 segment_strategy。

        Args:
            raw_command: 用户原始指令文本
            project_id: 关联项目 ID（可选）
            book_id: 关联书库 ID（可选）
            hint_force: 上层业务显式 hint（auto/force_split/no_split），优先级最高

        Returns:
            推荐策略：auto / force_split / no_split
        """
        # 0) 一键开关：关闭整套负载预估，全部回退 auto（补丁前行为）
        if not config_manager.get("load_estimator.enable", True):
            logger.info(
                "[LOAD_EST] 负载预估开关已关闭，回退策略: auto (hint=%s)",
                hint_force,
            )
            return normalize_segment_strategy(hint_force) or "auto"

        # 1) 上层 hint 优先级最高
        if hint_force is not None:
            hint = normalize_segment_strategy(hint_force)
            logger.info(
                "[LOAD_EST] 命中上层 hint_force=%s，直接采用", hint,
            )
            return hint

        # 2) 指令维度（纯字符串长度，零 IO）
        cmd_len = len(raw_command or "")
        cmd_level = (
            COMMAND_SHORT
            if cmd_len <= self._command_short_threshold()
            else COMMAND_LONG
        )

        # 3) 外部资源维度（只计数/元数据；异常整体降级为 auto）
        try:
            ext_level = await self._estimate_external(project_id, book_id)
        except Exception as exc:
            logger.warning(
                "[LOAD_EST] 外部负载预估异常，降级为 auto: %s", exc,
                exc_info=True,
            )
            return "auto"

        # 4) 专项2修复：token 预算校验（优先级最高的硬约束）
        #    外部资源存在时，将估算的上下文规模并入预算判定；
        #    超预算一律 force_split，防止长上下文溢出残留。
        est_tokens, budget = 0, 0
        try:
            over, est_tokens, budget = self.check_token_budget(raw_command)
        except Exception as exc:
            logger.warning("[LOAD_EST] token 预算校验异常，跳过: %s", exc)
            over = False

        # 5) 四类场景匹配（预算超限时直接升级为 force_split）
        if over:
            recommend: str = "force_split"
        elif cmd_level == COMMAND_SHORT and ext_level == EXTERNAL_LOW:
            recommend = "no_split"
        elif ext_level == EXTERNAL_HIGH:
            recommend = "force_split"   # SHORT+HIGH / LONG+HIGH
        else:
            recommend = "auto"          # LONG+LOW：走原始字符阈值轻量拆分

        logger.info(
            "[LOAD_EST] 场景判定: cmd=%s(%d字符) external=%s token_est=%d/budget=%d over=%s → recommend=%s"
            " (project=%s book=%s)",
            cmd_level, cmd_len, ext_level, est_tokens, budget, over,
            recommend, project_id, book_id,
        )
        return recommend  # type: ignore[return-value]

    # ── 外部资源维度预估（核心：只计数、只读元数据） ────────────

    async def _estimate_external(
        self, project_id: str | None, book_id: str | None
    ) -> str:
        """统计外部资源体量，返回 EXTERNAL_LOW / EXTERNAL_HIGH。"""
        card_total = 0
        doc_count = 0
        ai_parse_count = 0
        long_book = False

        # ── book_id 维度：书库卡片总数 + 书籍文件体量（仅 stat，不读正文） ──
        if book_id:
            if self._indexer is not None:
                card_total += await self._indexer.count_cards(source_book=book_id)
            book_file = self._books_dir / f"{book_id}.txt"
            try:
                if book_file.exists():
                    long_book = (
                        book_file.stat().st_size >= DEFAULT_LONG_BOOK_SIZE_BYTES
                    )
            except OSError as exc:
                logger.warning("[LOAD_EST] 书籍文件 stat 失败: %s - %s", book_file, exc)

        # ── project_id 维度：文档数 / 已解析资源 / 绑定书籍卡片总量 ──
        if project_id and self._project_manager is not None:
            pm = self._project_manager
            doc_count = await pm.count_project_documents(project_id)
            ai_parse_count = await pm.count_ai_parse_results(project_id)
            try:
                project = await pm.get_project(project_id)
                bind_ids = list(project.bind_book_ids) if project else []
            except Exception as exc:
                logger.warning("[LOAD_EST] 读取项目绑定书籍失败: %s", exc)
                bind_ids = []
            if bind_ids and self._indexer is not None:
                for bid in bind_ids:
                    card_total += await self._indexer.count_cards(source_book=bid)

        # ── 判定 ──
        card_threshold = self._external_high_card_threshold()
        if card_total >= card_threshold:
            return EXTERNAL_HIGH
        if doc_count >= DEFAULT_EXTERNAL_HIGH_DOC_THRESHOLD:
            return EXTERNAL_HIGH
        if ai_parse_count >= 1:
            return EXTERNAL_HIGH
        if long_book:
            return EXTERNAL_HIGH
        logger.debug(
            "[LOAD_EST] 外部资源统计: cards=%d docs=%d ai_parse=%d long_book=%s",
            card_total, doc_count, ai_parse_count, long_book,
        )
        return EXTERNAL_LOW
