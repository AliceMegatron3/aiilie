"""
tests/test_split_strategy_patch.py — 分段策略补丁验证（pytest 用例骨架）
========================================================================
补丁主题：短指令高负载任务无法触发分段逻辑漏洞（B1-08）。

覆盖用例：
  测试1：segment_strategy="force_split" + 300字符短指令 → 输出 2-3 个 Segment
         （修复「字符数不足导致未触发分段」测试报错场景）
  测试2：segment_strategy="no_split" → 无论文本多长只返回单个 Segment
  测试3：segment_strategy="auto" → 原有字符阈值/关键词判断逻辑与改造前完全一致
  测试4：模型层非法值兜底（CommandTask/TaskSubmitRequest 自动回退 auto）
  测试5：TaskManager.submit_task 非法策略防御回退 auto
  测试6：GlobalRouter 量化/文档学习/深度思考自动 force_split + 透传覆盖
  测试7：BookQuantifier / DocumentLearningEngine 任务构造自动标记 force_split

运行方式：
  python tests/test_split_strategy_patch.py          # 直接运行
  python -m pytest tests/test_split_strategy_patch.py -q -o addopts=""
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 沙箱隔离：所有持久化目录重定向到 OS 临时目录（safe-delete 垫片原生放行）
TMP_ROOT = Path(tempfile.mkdtemp(prefix="split_strategy_patch_"))
os.environ["APPDATA"] = str(TMP_ROOT / "appdata")

from core.database import DatabaseManager  # noqa: E402
from models.system import CommandRequest, PriorityLevel  # noqa: E402
from models.task import (  # noqa: E402
    CommandTask,
    TaskSubmitRequest,
    normalize_segment_strategy,
)
from services.command_splitter import CommandSplitter  # noqa: E402
from services.task_manager import TaskManager  # noqa: E402
from services.command_splitter import (  # noqa: E402
    SEGMENT_STRATEGY_AUTO,
    SEGMENT_STRATEGY_FORCE_SPLIT,
    SEGMENT_STRATEGY_NO_SPLIT,
)

_splitter = CommandSplitter()


def _make_task(raw_command: str, strategy: str = "auto") -> CommandTask:
    return CommandTask(raw_command=raw_command, segment_strategy=strategy)


# ══════════ 测试1：force_split 短指令强制多段 ══════════

def test_force_split_short_command_300_chars():
    """300字符短指令 + force_split → 输出 2-3 个有序 Segment（修复报错场景）。"""
    # 场景A：含句读符的短指令（自然拆句）
    raw_a = "数据加载与索引构建。" * 30  # 300字符
    assert len(raw_a) <= 300
    segs_a = _splitter.split(_make_task(raw_a, SEGMENT_STRATEGY_FORCE_SPLIT))
    assert 2 <= len(segs_a) <= 3, f"force_split 应产出2-3段，实际 {len(segs_a)}"
    assert [s.sequence_order for s in segs_a] == list(range(len(segs_a)))
    assert all(s.tail_context == {} for s in segs_a), "分片应初始化空 tail"
    # 内容覆盖检查（句读符在拆句时被剥离，两侧统一归一化后比较）
    import re
    raw_norm = re.sub(r"[。！？!?\n；;]+", "", raw_a)
    joined_norm = "".join(s.content_payload for s in segs_a).replace(" ", "")
    assert joined_norm == raw_norm, "内容应无丢失"

    # 场景B：无句读符的短指令（固定尺寸三等分）
    raw_b = "数" * 300
    segs_b = _splitter.split(_make_task(raw_b, SEGMENT_STRATEGY_FORCE_SPLIT))
    assert 2 <= len(segs_b) <= 3, f"无断句短指令也应分片，实际 {len(segs_b)}"
    assert "".join(s.content_payload for s in segs_b) == raw_b

    # 场景C：极短指令（<30字符）→ 逻辑节点分片（3个同载荷分片）
    raw_c = "请量化这本书"
    segs_c = _splitter.split(_make_task(raw_c, SEGMENT_STRATEGY_FORCE_SPLIT))
    assert len(segs_c) == 3, "极短指令应按逻辑节点生成3个分片"
    assert all(s.content_payload == raw_c for s in segs_c)


def test_force_split_tail_relay_in_pipeline():
    """force_split 多段在流水线中尾巴可正常接力（processed_count 递增）。"""
    async def _run():
        db = DatabaseManager(db_path=TMP_ROOT / "db_tail_relay.db")
        await db.initialize()
        from services.segment_pipeline import SegmentPipeline
        from services.result_merger import ResultMerger
        from services.tail_context_manager import TailContextManager
        from services.temp_file_manager import TempFileManager

        async def hook(content, tail, source):
            n = tail.get("processed_count", 0) + 1
            return f"R{n}", {**tail, "processed_count": n}

        temp_mgr = TempFileManager(temp_root=TMP_ROOT / "temp_relay")
        tail_mgr = TailContextManager(temp_root=TMP_ROOT / "tails_relay")
        pipe = SegmentPipeline(db, temp_mgr, execution_hook=hook,
                               tail_manager=tail_mgr, max_db_payload_bytes=500)
        tm = TaskManager(db, _splitter, pipe, ResultMerger(), temp_mgr,
                         tail_manager=tail_mgr)
        await tm.initialize()
        task = await tm.submit_task("数据加载。" * 30,
                                    segment_strategy=SEGMENT_STRATEGY_FORCE_SPLIT)
        assert len(task.segments) >= 2, "force_split 任务应被拆分"
        result = await tm.process_next()
        from models.task import TaskStatus
        assert result.status == TaskStatus.COMPLETED
        # 尾巴接力：最后一个分段的 processed_count 应等于分段总数
        last_seg = sorted(task.segments, key=lambda s: s.sequence_order)[-1]
        assert last_seg.tail_context.get("processed_count") == len(task.segments), \
            "尾巴应跨分段接力累计"
        await db.close()

    asyncio.run(_run())


# ══════════ 测试2：no_split 永远单段 ══════════

def test_no_split_always_single_segment():
    """no_split：无论文本多长（含章节标记），永远只返回单个 Segment。"""
    raw = ("第一章 环境描写。" * 300)  # 2700字符 + 章节标记，auto 会拆多段
    segs = _splitter.split(_make_task(raw, SEGMENT_STRATEGY_NO_SPLIT))
    assert len(segs) == 1, f"no_split 应只产出1段，实际 {len(segs)}"
    assert segs[0].content_payload == raw
    assert segs[0].sequence_order == 0 and segs[0].tail_context == {}


# ══════════ 测试3：auto 原有逻辑与改造前完全一致 ══════════

def test_auto_short_text_single_segment():
    """auto + 短文本无关键词 → 单一分段（原有逻辑）。"""
    raw = "帮我写一句环境描写。" * 3  # 33字符，无触发关键词
    segs = _splitter.split(_make_task(raw, SEGMENT_STRATEGY_AUTO))
    assert len(segs) == 1


def test_auto_long_text_splits():
    """auto + 超2000字符长文本 → 多分段（原有字符阈值逻辑）。"""
    raw = "环境描写细节。" * 300  # 2100字符
    segs = _splitter.split(_make_task(raw, SEGMENT_STRATEGY_AUTO))
    assert len(segs) >= 2


def test_auto_keyword_triggers_split():
    """auto + 触发关键词 + 长文本 → 多分段（原有关键词触发决策 + 字数拆分）。"""
    # 说明：短关键词文本在原版逻辑中虽触发拆分决策，但 by_length 对短文本
    # 只产出单段；用长文本验证"关键词+字数"原有组合行为。
    raw = "请生成全书大纲，" * 300  # 2400字符，含触发关键词
    segs = _splitter.split(_make_task(raw, SEGMENT_STRATEGY_AUTO))
    assert len(segs) >= 2


def test_auto_chapter_strategy_unchanged():
    """auto + 章节标记长文本 → by_chapter 拆分，分段数 = 章节数（原有策略选择）。"""
    raw = "第一章 开端。" * 400  # 3200字符，触发阈值后按章节拆分
    segs = _splitter.split(_make_task(raw, SEGMENT_STRATEGY_AUTO))
    assert len(segs) == 400


def test_default_strategy_is_auto():
    """未显式设置 segment_strategy 的任务默认 auto（存量任务行为不变）。"""
    task = CommandTask(raw_command="任意指令")
    assert task.segment_strategy == SEGMENT_STRATEGY_AUTO


# ══════════ 测试4：模型层非法值兜底 ══════════

def test_model_illegal_strategy_falls_back_to_auto():
    """Pydantic 校验：非法 segment_strategy 自动回退 auto。"""
    for bad in ("illegal_xyz", "chapter", "", None, 123):
        task = CommandTask(raw_command="x", segment_strategy=bad)
        assert task.segment_strategy == SEGMENT_STRATEGY_AUTO, f"非法值 {bad!r} 应回退 auto"
    req = TaskSubmitRequest(raw_command="x", segment_strategy="whatever")
    assert req.segment_strategy == SEGMENT_STRATEGY_AUTO
    # 大小写容忍
    assert normalize_segment_strategy("FORCE_SPLIT") == SEGMENT_STRATEGY_FORCE_SPLIT
    assert normalize_segment_strategy(" No_Split ") == SEGMENT_STRATEGY_NO_SPLIT


# ══════════ 测试5：TaskManager.submit_task 防御 ══════════

def test_task_manager_illegal_strategy_defensive_fallback():
    """submit_task 收到非法 segment_strategy 自动回退 auto，不崩溃。"""
    async def _run():
        db = DatabaseManager(db_path=TMP_ROOT / "db_defense.db")
        await db.initialize()
        from services.segment_pipeline import SegmentPipeline
        from services.result_merger import ResultMerger
        from services.temp_file_manager import TempFileManager

        temp_mgr = TempFileManager(temp_root=TMP_ROOT / "temp_defense")
        tm = TaskManager(db, _splitter,
                         SegmentPipeline(db, temp_mgr),
                         ResultMerger(), temp_mgr)
        await tm.initialize()
        task = await tm.submit_task("短指令", segment_strategy="bad_strategy_xyz")
        assert task.segment_strategy == SEGMENT_STRATEGY_AUTO
        assert len(task.segments) == 1
        # 合法值不受影响
        task_f = await tm.submit_task("短指令", segment_strategy=SEGMENT_STRATEGY_FORCE_SPLIT)
        assert task_f.segment_strategy == SEGMENT_STRATEGY_FORCE_SPLIT
        assert len(task_f.segments) >= 2
        await db.close()

    asyncio.run(_run())


# ══════════ 测试6：GlobalRouter 自动标记与透传 ══════════

def test_global_router_force_split_marking():
    """量化/文档学习自动 force_split；深度思考自动 force_split；普通问答 auto；透传覆盖。"""
    async def _run():
        from core.task_manager import TaskManager as CoreTaskManager
        from services.global_router import GlobalRouter
        from services.priority_queue import PriorityTaskQueue

        db = DatabaseManager(db_path=TMP_ROOT / "db_router.db")
        await db.initialize()
        from services.segment_pipeline import SegmentPipeline
        from services.result_merger import ResultMerger
        from services.temp_file_manager import TempFileManager

        temp_mgr = TempFileManager(temp_root=TMP_ROOT / "temp_router")
        tm = TaskManager(db, _splitter, SegmentPipeline(db, temp_mgr),
                         ResultMerger(), temp_mgr)
        await tm.initialize()

        class FakeMonitor:
            def get_health_report(self):
                return {"resource": {"memory_percent": 10.0}}

            def is_locked_for_quantization(self) -> bool:
                return False

            def acquire_quantization_lock(self):
                pass

            def release_quantization_lock(self):
                pass

        class FakeApplier:
            async def fetch_active_rules(self, scope=None, context_features=None):
                return []

        class FakeTrigger:
            pass

        router = GlobalRouter(
            task_queue=PriorityTaskQueue(),
            system_monitor=FakeMonitor(),
            task_manager=CoreTaskManager(db),
            reflection_trigger=FakeTrigger(),
            optimization_applier=FakeApplier(),
            model_dispatcher=None,
            project_manager=None,
            batch1_task_manager=tm,
        )

        # QUANTIZATION 意图 → force_split（短指令多段）
        req_q = CommandRequest(command="请量化这本书", options={"is_command_mode": True})
        resp_q = await router.route_command(req_q)
        assert resp_q["engine"] == "batch1"
        assert resp_q["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT
        task_q = tm._tasks[resp_q["task_id"]]
        assert len(task_q.segments) >= 2, "量化短指令应被强制分段"

        # DOC_LEARNING 意图 → force_split
        req_d = CommandRequest(command="帮我解析文档", options={"is_command_mode": True})
        resp_d = await router.route_command(req_d)
        assert resp_d["intent"] == "DOC_LEARNING"
        assert resp_d["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT

        # DEEP_THINK（mode=think 短指令）→ force_split
        req_t = CommandRequest(command="分析这段设定", options={
            "mode": "think", "is_command_mode": True})
        resp_t = await router.route_command(req_t)
        assert resp_t["engine"] == "batch1"
        assert resp_t["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT

        # 透传覆盖：量化指令 + options.segment_strategy=no_split → 单段
        req_n = CommandRequest(command="请量化这本书", options={
            "is_command_mode": True, "segment_strategy": SEGMENT_STRATEGY_NO_SPLIT})
        resp_n = await router.route_command(req_n)
        task_n = tm._tasks[resp_n["task_id"]]
        assert resp_n["segment_strategy"] == SEGMENT_STRATEGY_NO_SPLIT
        assert len(task_n.segments) == 1

        # 透传非法值忽略：保持默认 force_split
        # 注意：去重限流（同指令 1 分钟最多 2 次）会拦截第 3 次同文本，故换用不同文本
        req_bad = CommandRequest(command="请量化另一本书", options={
            "is_command_mode": True, "segment_strategy": "garbage"})
        resp_bad = await router.route_command(req_bad)
        assert resp_bad["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT

        # 普通轻量创作（rapid + 短文本）→ 旧闭包路径，不强制分段
        req_s = CommandRequest(command="写一句话", options={
            "mode": "rapid", "is_command_mode": True})
        resp_s = await router.route_command(req_s)
        assert resp_s.get("engine") is None  # 走旧闭包（短任务）
        await db.close()

    asyncio.run(_run())


# ══════════ 测试7：重负载业务任务构造自动 force_split ══════════

def test_book_quantifier_marks_force_split():
    """BookQuantifier 提交的量化任务 segment_strategy=force_split。"""
    async def _run():
        from core.task_manager import TaskManager as CoreTaskManager
        from services.quantifier import BookQuantifier

        db = DatabaseManager(db_path=TMP_ROOT / "db_quant.db")
        await db.initialize()
        core_tm = CoreTaskManager(db)

        class _Stub:
            pass

        quant = BookQuantifier(
            db=db, task_manager=core_tm,
            indexer=_Stub(), registry=_Stub(), strategy=_Stub(),
        )
        task_id = await quant.submit_quantize_task("book_001")
        row = await db.get_task(task_id)
        assert row is not None
        assert row["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT, \
            f"量化任务应标记 force_split，实际 {row['segment_strategy']!r}"
        await db.close()

    asyncio.run(_run())


def test_document_learning_marks_force_split():
    """DocumentLearningEngine 提交的文档学习任务 segment_strategy=force_split。"""
    async def _run():
        from core.task_manager import TaskManager as CoreTaskManager
        from services.learning_engine import DocumentLearningEngine

        db = DatabaseManager(db_path=TMP_ROOT / "db_learn.db")
        await db.initialize()
        core_tm = CoreTaskManager(db)

        class FakeDoc:
            project_id = "proj_1"
            raw_content = "x" * 100

        class FakePM:
            async def get_document(self, doc_id):
                return FakeDoc()

        class _Stub:
            pass

        engine = DocumentLearningEngine(
            db=db, task_manager=core_tm, project_manager=FakePM(),
            dispatcher=_Stub(),
        )
        task_id = await engine.submit_learning_task("doc_001")
        row = await db.get_task(task_id)
        assert row is not None
        assert row["segment_strategy"] == SEGMENT_STRATEGY_FORCE_SPLIT, \
            f"文档学习任务应标记 force_split，实际 {row['segment_strategy']!r}"
        await db.close()

    asyncio.run(_run())


if __name__ == "__main__":
    import traceback

    tests = [
        test_force_split_short_command_300_chars,
        test_force_split_tail_relay_in_pipeline,
        test_no_split_always_single_segment,
        test_auto_short_text_single_segment,
        test_auto_long_text_splits,
        test_auto_keyword_triggers_split,
        test_auto_chapter_strategy_unchanged,
        test_default_strategy_is_auto,
        test_model_illegal_strategy_falls_back_to_auto,
        test_task_manager_illegal_strategy_defensive_fallback,
        test_global_router_force_split_marking,
        test_book_quantifier_marks_force_split,
        test_document_learning_marks_force_split,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✔ {t.__name__}")
        except Exception:
            failed += 1
            print(f"✘ {t.__name__}")
            traceback.print_exc()
    shutil.rmtree(str(TMP_ROOT), ignore_errors=True)
    print(f"\n{'全部通过' if failed == 0 else f'{failed} 个用例失败'} ({len(tests)} 个用例)")
    sys.exit(1 if failed else 0)
