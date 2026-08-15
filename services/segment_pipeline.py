"""
services/segment_pipeline.py — 执行流水线与尾巴管理
====================================================
负责按顺序执行 Segment，管理 tail_context 的传递，
确保"尾巴"数据能够无损地传递给下一个分段。

P0 修复（本批次）：
  1. 大 result_content 不再写入 SQLite：超过 task.max_db_payload_bytes 阈值时
     result_content 字段置空，只持久化 output_path（临时文件路径），
     防止数据库几何膨胀；短内容保持原逻辑入库（向后兼容）；
  2. 尾巴管理全部移交 TailContextManager（独立模块），
     删除内部硬编码尾巴传递逻辑；支持大尾巴落盘双模式；
  3. 分段执行异常时，将失败现场的尾巴单独落盘快照，供任务重启恢复。

业务逻辑执行钩子留空，通过依赖注入由后续批次实现。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.config_manager import config_manager
from core.database import DatabaseManager
from models.task import Segment, SegmentStatus
from services.tail_context_manager import TailContextManager
from services.temp_file_manager import TempFileManager

logger = logging.getLogger(__name__)

# 执行钩子类型：接收 (content_payload, tail_context, model_source, retry_prompt) -> raw_json_string
ExecutionHook = Callable[
    [str, dict[str, Any], str, str | None],
    Awaitable[str],
]

# 默认 DB 载荷阈值：10KB，超过即不写入 SQLite
DEFAULT_MAX_DB_PAYLOAD_BYTES = 10 * 1024


async def _default_execution_hook(
    content_payload: str,
    tail_context: dict[str, Any],
    model_source: str,
    retry_prompt: str | None = None,
) -> str:
    """
    默认执行钩子（占位）。
    批次3将通过依赖注入替换此钩子，实现实际的模型调用逻辑。

    当前行为：返回包含 result_content 和 new_tail 的合法 JSON 字符串。
    """
    import json
    logger.debug(
        "默认执行钩子被调用 (model_source=%s), 尝试重试: %s", model_source, retry_prompt is not None
    )
    # 在 tail 中记录已处理的段数
    processed = tail_context.get("processed_count", 0) + 1
    new_tail = {
        **tail_context,
        "processed_count": processed,
        "last_content_length": len(content_payload),
    }
    result = f"[PLACEHOLDER] 已处理内容 ({len(content_payload)} 字符)"
    if retry_prompt:
        result += " (已按重试提示校正)"
        
    return json.dumps({
        "result_content": result,
        "new_tail": new_tail
    }, ensure_ascii=False)


class SegmentPipeline:
    """
    分段执行流水线。

    核心职责：
    1. 按 sequence_order 顺序执行分段
    2. 读取前一段的 tail_context 作为输入（经 TailContextManager 物化）
    3. 将执行结果和新的 tail_context 写入数据库和临时文件
    4. 确保尾巴数据在分段间无损传递
    """

    def __init__(
        self,
        db: DatabaseManager,
        temp_manager: TempFileManager,
        execution_hook: ExecutionHook | None = None,
        tail_manager: TailContextManager | None = None,
        max_db_payload_bytes: int | None = None,
        allow_large_payload_in_db: bool | None = None,
    ) -> None:
        self._db = db
        self._temp_manager = temp_manager
        self._hook = self._normalize_hook(execution_hook or _default_execution_hook)
        self._tail_manager = tail_manager
        # DB 载荷阈值：从配置读取，构造参数优先（便于测试）
        if max_db_payload_bytes is None:
            max_db_payload_bytes = int(
                config_manager.get(
                    "task.max_db_payload_bytes", DEFAULT_MAX_DB_PAYLOAD_BYTES
                )
            )
        self._max_db_payload_bytes = max_db_payload_bytes
        if allow_large_payload_in_db is None:
            allow_large_payload_in_db = bool(
                config_manager.get("task.allow_large_payload_in_db", False)
            )
        self._allow_large_payload_in_db = allow_large_payload_in_db
        logger.info(
            "分段流水线初始化: DB载荷阈值=%d字节, 允许大载荷入库=%s, 尾巴管理器=%s",
            self._max_db_payload_bytes, self._allow_large_payload_in_db,
            "已挂载" if self._tail_manager else "未挂载(纯内存模式)",
        )

    @staticmethod
    def _normalize_hook(hook: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        """向后兼容适配执行钩子签名。

        历史钩子签名为 (content_payload, tail_context, model_source) 三参数；
        当前流水线会以四参数 (…, retry_prompt) 调用钩子以支持重试校正。
        通过签名内省，对三参数钩子自动包装，避免 TypeError 兼容性回归。
        """
        import inspect
        try:
            sig = inspect.signature(hook)
            params = sig.parameters.values()
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            min_pos = sum(
                1 for p in params
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD)
            )
            accepts_four = has_varargs or min_pos >= 4
        except (TypeError, ValueError):
            # 无法内省（如 C 扩展可调用对象）时按四参数调用
            accepts_four = True

        if accepts_four:
            return hook

        async def _three_arg_wrapper(
            content_payload: str,
            tail_context: dict[str, Any],
            model_source: str,
            retry_prompt: str | None = None,
        ) -> str:
            return await hook(content_payload, tail_context, model_source)

        logger.info("执行钩子已按三参数签名适配包装")
        return _three_arg_wrapper

    def set_execution_hook(self, hook: ExecutionHook) -> None:
        """注入自定义执行钩子（批次3使用）。"""
        self._hook = self._normalize_hook(hook)
        logger.info("执行钩子已更新")

    # ── DB 载荷分级 ────────────────────────────────────────────

    def _should_store_in_db(self, result_content: str) -> bool:
        """
        P0 修复：判断 result_content 是否允许写入 SQLite。

        - allow_large_payload_in_db 开启：始终入库（旧逻辑回退开关）；
        - 内容字节数 <= max_db_payload_bytes：入库；
        - 超过阈值：不入库，只保留 output_path 磁盘文件。
        """
        if self._allow_large_payload_in_db:
            return True
        try:
            size = len(result_content.encode("utf-8"))
        except (AttributeError, UnicodeEncodeError):
            return False
        return size <= self._max_db_payload_bytes

    def _parse_json_robust(self, text: str) -> dict[str, Any]:
        """使用 dirtyjson 或等效正则进行 JSON 宽松解析"""
        import json, re
        try:
            import dirtyjson
            return dirtyjson.loads(text)
        except ImportError:
            pass

        # 尝试剥离 markdown code block
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        
        text = text.strip()
        # 尝试正则提取大括号内容
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as e:
                raise ValueError(f"无法解析JSON (正则提取后): {e}")
                
        # 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"无效的JSON格式: {e}")

    @staticmethod
    def _normalize_hook_result(raw_output: Any) -> tuple[str, dict[str, Any]]:
        """兼容三种执行钩子返回契约，统一归一化为 (result_content, new_tail)：

        1. JSON 字符串（AI 钩子）：{"result_content": ..., "new_tail": ...}
        2. 二元组/二元列表 (result_content, new_tail)（占位/测试钩子历史契约）
        3. dict（已解析对象）
        """
        if isinstance(raw_output, dict):
            return (
                str(raw_output.get("result_content", "")),
                dict(raw_output.get("new_tail") or {}),
            )
        if isinstance(raw_output, (tuple, list)) and len(raw_output) == 2:
            content, tail = raw_output
            return str(content or ""), dict(tail or {})
        if isinstance(raw_output, str):
            parsed = SegmentPipeline._parse_json_robust(raw_output)
            return (
                str(parsed.get("result_content", "")),
                dict(parsed.get("new_tail") or {}),
            )
        # 兜底：其余类型整体视为内容
        return str(raw_output), {}

    def _materialize_tail(self, tail_ref: dict[str, Any] | None) -> dict[str, Any]:
        """统一尾巴读取入口（TailContextManager 挂载时物化，否则原样）。"""
        if self._tail_manager is not None:
            return self._tail_manager.materialize(tail_ref)
        return dict(tail_ref or {})

    def _store_tail(
        self,
        task_id: str,
        segment_id: str,
        tail: dict[str, Any],
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """统一尾巴存储入口（架构整改 1.1：携带 branch_id 保证分支隔离）。"""
        if self._tail_manager is not None:
            return self._tail_manager.store(task_id, segment_id, tail, branch_id=branch_id)
        return tail

    async def execute(
        self,
        segment: Segment,
        model_source: str = "local",
        branch_id: str | None = None,
    ) -> Segment:
        """
        执行单个分段。

        流程：
        1. 标记分段为 RUNNING
        2. 物化 tail_context（含失败现场快照恢复）
        3. 调用执行钩子处理业务逻辑
        4. 按阈值分级保存结果（DB + 临时文件）
        5. 返回更新后的 Segment（含 new_tail）

        架构整改 1.1：branch_id 注入尾巴顶层，平行宇宙分支严格隔离。
        """
        logger.info(
            "开始执行分段 %s (任务 %s, 顺序 %d)",
            segment.segment_id, segment.parent_task_id, segment.sequence_order,
        )

        # 1. 标记为运行中
        segment.status = SegmentStatus.RUNNING
        segment.started_at = datetime.now(timezone.utc)
        await self._db.update_segment_status(
            segment.segment_id, "RUNNING"
        )

        # 2. 物化 tail_context
        tail = self._materialize_tail(segment.tail_context)
        # 架构整改 1.1：分支隔离——尾巴顶层强制携带 branch_id（若任务绑定分支）
        if branch_id and "branch_id" not in tail:
            tail["branch_id"] = branch_id

        try:
            # 2.1 任务重启恢复：检查该分段是否有失败现场快照
            if self._tail_manager is not None:
                snapshot = self._tail_manager.load_failure_snapshot(
                    segment.parent_task_id, segment.segment_id
                )
                if snapshot:
                    logger.info(
                        "分段 %s 检测到失败现场快照，注入恢复尾巴",
                        segment.segment_id,
                    )
                    tail = {**tail, **snapshot}

            # 3. 调用执行钩子，带AI重试与宽松解析
            max_retries = 3
            retry_prompt = None
            result_content = ""
            new_tail = {}

            attempt = 0
            while attempt <= max_retries:
                try:
                    # 保护钩子：代码层硬拦截（补丁G）
                    # 返回值 = 允许的最大重试轮次（混沌模式强制 0，pipeline 据此终结重试）
                    try:
                        from services.emotion_engine.agent_runtime_protect import agent_runtime_protector
                        max_retries = await agent_runtime_protector.pre_execution_hook(
                            segment, tail, attempt, max_retries
                        )
                    except InterruptedError as e:
                        logger.warning(str(e))
                        # 触发了硬熔断，终止后续重试
                        if attempt == 0:
                            raise e # 如果第一次就失败，则抛出
                        break # 保留上一次的结果
                    
                    # 获取模型输出（如果带 retry_prompt，模型应当纠正）
                    import asyncio
                    try:
                        raw_output = await asyncio.wait_for(
                            self._hook(
                                segment.content_payload, tail, model_source, retry_prompt
                            ),
                            timeout=120.0
                        )
                    except asyncio.TimeoutError:
                        # 补丁G：超时熔断——推理超时不再重试（重试只会再次超时，浪费算力）
                        logger.warning("分段 %s 推理超时 (>120s)，触发超时熔断（不重试）。", segment.segment_id)
                        raise TimeoutError("分段推理超时熔断（>120s）")
                    
                    parsed = self._normalize_hook_result(raw_output)
                    result_content, new_tail = parsed
                    # P0-5：模型返回后立即落 SQLite checkpoint，崩溃恢复不以临时文件为唯一依据。
                    # 保留完整中间结果供恢复，摘要字段用于列表/诊断场景，最终状态仍按原阈值控制。
                    try:
                        await self._db.update_segment_checkpoint(
                            segment.segment_id,
                            checkpoint_content=result_content,
                            checkpoint_tail=new_tail,
                            checkpoint_summary=result_content[:4096],
                        )
                    except Exception:
                        logger.exception("分段 %s 中间 checkpoint 写入失败", segment.segment_id)
                    break
                except Exception as parse_exc:
                    if attempt < max_retries:
                        logger.warning("分段 %s 解析失败 (尝试 %d/%d): %s", segment.segment_id, attempt + 1, max_retries, parse_exc)
                        retry_prompt = "你的上一次输出并非合法 JSON，请仅输出修正后的合法 JSON，不包含任何额外解释。"
                        # 每次重试前清空部分上下文避免重复
                        tail = {} 
                    else:
                        logger.error("分段 %s 解析失败，已达到最大重试次数", segment.segment_id)
                        raise ValueError(f"JSON 解析最终失败: {parse_exc}")
                finally:
                    attempt += 1

            # 4. 将结果写入临时文件
            # 使用 pathlib.Path 确保 Windows 路径兼容
            seg_dir = self._temp_manager.get_segment_dir(segment.segment_id)
            output_file: Path = seg_dir / f"output_{segment.sequence_order}.txt"
            output_file.write_text(result_content, encoding="utf-8")

            # 5. 尾巴经 TailContextManager 统一存储（可能落盘为标记）
            stored_tail = self._store_tail(
                segment.parent_task_id, segment.segment_id, new_tail or {},
                branch_id=branch_id,
            )

            # 6. 更新分段状态
            segment.status = SegmentStatus.COMPLETED
            segment.completed_at = datetime.now(timezone.utc)
            segment.output_path = str(output_file)
            segment.tail_context = stored_tail

            # P0：按阈值决定 result_content 是否入库
            db_result_content = (
                result_content if self._should_store_in_db(result_content) else None
            )
            segment.result_content = db_result_content  # 大内容不入内存模型

            await self._db.update_segment_status(
                segment.segment_id,
                "COMPLETED",
                result_content=db_result_content,  # 防止 SQLite 膨胀
                output_path=str(output_file),
                new_tail=stored_tail,
            )

            # 恢复成功后清除失败现场快照
            if self._tail_manager is not None:
                self._tail_manager.discard_failure_snapshot(
                    segment.parent_task_id, segment.segment_id
                )

            logger.info(
                "分段 %s 执行完成，输出文件: %s (DB载荷=%s)",
                segment.segment_id, output_file,
                "已入库" if db_result_content is not None else "磁盘外置",
            )

        except Exception as exc:
            segment.status = SegmentStatus.FAILED
            segment.completed_at = datetime.now(timezone.utc)
            segment.error_message = str(exc)
            await self._db.update_segment_status(
                segment.segment_id, "FAILED", error_message=str(exc)
            )
            # P1：异常现场尾巴单独落盘，供任务重启恢复
            if self._tail_manager is not None:
                try:
                    self._tail_manager.save_failure_snapshot(
                        segment.parent_task_id, segment.segment_id, tail
                    )
                except Exception as snap_exc:
                    logger.error("失败现场快照保存异常: %s", snap_exc)
            logger.error(
                "分段 %s 执行失败: %s", segment.segment_id, exc, exc_info=True
            )

        return segment

    async def execute_all(
        self,
        segments: list[Segment],
        model_source: str = "local",
        branch_id: str | None = None,
    ) -> list[Segment]:
        """
        按顺序执行所有分段，自动传递 tail_context。

        关键逻辑：每个分段完成后，将其 new_tail
        注入到下一个分段的 tail_context 中。
        所有尾巴传递统一经过 TailContextManager 物化/存储。
        架构整改 1.1：branch_id 贯穿全程注入尾巴，保证分支隔离。
        """
        # 按 sequence_order 排序
        sorted_segments = sorted(segments, key=lambda s: s.sequence_order)
        results: list[Segment] = []
        current_tail: dict[str, Any] = {}

        for seg in sorted_segments:
            # 将前一段的尾巴物化后注入当前段（落盘尾巴先还原）
            prev_tail = self._materialize_tail(current_tail)
            own_tail = self._materialize_tail(seg.tail_context)
            seg.tail_context = {**prev_tail, **own_tail}

            executed = await self.execute(seg, model_source, branch_id=branch_id)
            results.append(executed)

            if executed.status == SegmentStatus.COMPLETED:
                # 传递尾巴给下一段（可能是落盘标记，体积极小）
                current_tail = executed.tail_context
            else:
                # 分段失败，记录但继续（可配置为中断）
                logger.warning(
                    "分段 %s 执行失败，后续分段将使用上一次成功的 tail_context",
                    executed.segment_id,
                )

        return results
