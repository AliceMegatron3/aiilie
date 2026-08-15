"""
tests/test_load_estimator.py — 指令负载智能识别调度优化验证（pytest 用例）
==========================================================================
覆盖 B1-09 四类场景 + 开关/优先级/降级/路由集成：

  用例1：短指令 + 少量卡片（短指令-短内容）→ 推荐 no_split，单 segment
  用例2：短指令 + 大量书库卡片（短指令-长内容）→ 推荐 force_split，多 Segment + 尾巴接力
  用例3：长指令 + 大量外部资源（长指令-长内容）→ force_split
  用例4：长指令、几乎无外部资源（长指令-短内容）→ auto
  用例5：关闭 load_estimator.enable → 全部回退 auto（hint 除外）
  附加：hint_force 优先级最高；预估异常降级 auto；GlobalRouter 集成；业务模块 hint

运行方式：
  python tests/test_load_estimator.py          # 直接运行
  python -m pytest tests/test_load_estimator.py -q -o addopts=""
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 沙箱隔离：持久化目录重定向到 OS 临时目录
TMP_ROOT = Path(tempfile.mkdtemp(prefix="load_estimator_"))
os.environ["APPDATA"] = str(TMP_ROOT / "appdata")

from core.config_manager import config_manager  # noqa: E402
from models.system import CommandRequest  # noqa: E402
from services.command_splitter import CommandSplitter  # noqa: E402
from services.load_estimator import LoadEstimator  # noqa: E402

_splitter = CommandSplitter()


# ── 可配置 Fake（只暴露计数接口，验证 LoadEstimator 不读正文） ──

class FakeIndexer:
    def __init__(self, counts: dict[str, int] | None = None, raise_on_count: bool = False):
        self._counts = counts or {}
        self._raise = raise_on_count
        self.body_read_calls = 0  # 计数：若 >0 说明预估阶段读取了正文（应禁止）

    async def count_cards(self, source_book: str | None = None) -> int:
        if self._raise:
            raise RuntimeError("卡片库连接异常")
        key = source_book or "__total__"
        return self._counts.get(key, 0)


class FakePM:
    def __init__(self, doc_count=0, ai_parse_count=0, bind_book_ids=None):
        self.doc_count = doc_count
        self.ai_parse_count = ai_parse_count
        self.bind_book_ids = bind_book_ids or []

    async def count_project_documents(self, project_id: str) -> int:
        return self.doc_count

    async def count_ai_parse_results(self, project_id: str) -> int:
        return self.ai_parse_count

    async def get_project(self, project_id: str):
        class _P:
            bind_book_ids = self.bind_book_ids
        return _P()


def _make_estimator(card_counts: dict[str, int] | None = None,
                    doc_count: int = 0, ai_parse_count: int = 0,
                    bind_book_ids=None, raise_on_count: bool = False) -> LoadEstimator:
    return LoadEstimator(
        indexer=FakeIndexer(card_counts, raise_on_count=raise_on_count),
        project_manager=FakePM(doc_count, ai_parse_count, bind_book_ids),
    )


# ══════════ 用例1：短指令-短内容 → no_split ══════════

def test_short_command_low_external_recommends_no_split():
    """短指令(≤1200字符) + 少量卡片(2张) → no_split（单节点执行，不做多余分段）。"""
    est = _make_estimator(card_counts={"book_b1": 2})
    async def _run():
        rec = await est.estimate("帮我写一个短场景描写", book_id="book_b1")
        assert rec == "no_split"
        # 单 segment 确认
        from models.task import CommandTask
        segs = _splitter.split(CommandTask(
            raw_command="帮我写一个短场景描写", segment_strategy=rec))
        assert len(segs) == 1
    asyncio.run(_run())


# ══════════ 用例2：短指令-长内容 → force_split + 尾巴接力 ══════════

def test_short_command_high_external_recommends_force_split():
    """短指令(300字符) + 12张书库卡片 → force_split，多 Segment，尾巴接力。"""
    est = _make_estimator(card_counts={"book_b1": 12})
    async def _run():
        rec = await est.estimate(
            "数据加载与索引构建。" * 30, book_id="book_b1")
        assert rec == "force_split"

        from models.task import CommandTask
        task = CommandTask(raw_command="数据加载与索引构建。" * 30,
                           segment_strategy=rec)
        segs = _splitter.split(task)
        assert 2 <= len(segs) <= 3, f"应产出2-3段，实际 {len(segs)}"

        # 引擎链路尾巴接力
        from core.database import DatabaseManager
        from services.segment_pipeline import SegmentPipeline
        from services.result_merger import ResultMerger
        from services.tail_context_manager import TailContextManager
        from services.temp_file_manager import TempFileManager
        from services.task_manager import TaskManager
        from models.task import TaskStatus

        async def hook(content, tail, source):
            n = tail.get("processed_count", 0) + 1
            return f"R{n}", {**tail, "processed_count": n}

        db = DatabaseManager(db_path=TMP_ROOT / "db_case2.db")
        await db.initialize()
        temp_mgr = TempFileManager(temp_root=TMP_ROOT / "temp_case2")
        tail_mgr = TailContextManager(temp_root=TMP_ROOT / "tails_case2")
        tm = TaskManager(db, _splitter,
                         SegmentPipeline(db, temp_mgr, execution_hook=hook,
                                         tail_manager=tail_mgr,
                                         max_db_payload_bytes=500),
                         ResultMerger(), temp_mgr, tail_manager=tail_mgr)
        await tm.initialize()
        t = await tm.submit_task("数据加载与索引构建。" * 30,
                                 segment_strategy="force_split")
        assert len(t.segments) >= 2
        res = await tm.process_next()
        assert res.status == TaskStatus.COMPLETED
        last = sorted(t.segments, key=lambda s: s.sequence_order)[-1]
        assert last.tail_context.get("processed_count") == len(t.segments), \
            "尾巴应跨分段接力累计"
        await db.close()
    asyncio.run(_run())


# ══════════ 用例3：长指令-长内容 → force_split ══════════

def test_long_command_high_external_recommends_force_split():
    """长指令(>1200字符) + 大量外部资源 → force_split。"""
    est = _make_estimator(card_counts={"book_b1": 30})
    async def _run():
        rec = await est.estimate("环境描写细节。" * 200, book_id="book_b1")
        assert rec == "force_split"
        from models.task import CommandTask
        segs = _splitter.split(CommandTask(raw_command="环境描写细节。" * 200,
                                           segment_strategy=rec))
        assert len(segs) >= 2
    asyncio.run(_run())


# ══════════ 用例4：长指令-短内容 → auto ══════════

def test_long_command_low_external_recommends_auto():
    """长指令(>1200字符)、几乎无外部资源 → auto（按旧阈值轻量拆分）。"""
    est = _make_estimator()  # 无外部资源
    async def _run():
        rec = await est.estimate("环境描写细节。" * 300)  # 2400字符>1200
        assert rec == "auto"
        from models.task import CommandTask
        segs = _splitter.split(CommandTask(raw_command="环境描写细节。" * 300,
                                           segment_strategy=rec))
        assert len(segs) >= 2  # auto 长文本（>2000字符）仍按旧阈值拆分
    asyncio.run(_run())


# ══════════ 用例5：关闭开关 → 回退 auto ══════════

def test_disable_estimator_falls_back_auto():
    """关闭 load_estimator.enable：短指令+12卡片也回退 auto（补丁前行为）。"""
    est = _make_estimator(card_counts={"book_b1": 12})
    _orig = config_manager._config.get("load_estimator", {}).get("enable", True)
    config_manager._config.setdefault("load_estimator", {})["enable"] = False
    try:
        async def _run():
            rec = await est.estimate("请量化这本书", book_id="book_b1")
            assert rec == "auto"
            # hint 不受开关影响（B1-08 显式业务标记保留）
            rec2 = await est.estimate("请量化这本书", book_id="book_b1",
                                      hint_force="force_split")
            assert rec2 == "force_split"
        asyncio.run(_run())
    finally:
        config_manager._config["load_estimator"]["enable"] = _orig


# ══════════ 附加：hint 优先级 + 异常降级 ══════════

def test_hint_force_overrides_estimation():
    """hint_force 优先级高于自动预估：大量卡片 + hint=no_split → no_split。"""
    est = _make_estimator(card_counts={"book_b1": 50})
    async def _run():
        rec = await est.estimate("短指令", book_id="book_b1",
                                 hint_force="no_split")
        assert rec == "no_split"
        # 非法 hint 归一化回 auto
        rec2 = await est.estimate("短指令", book_id="book_b1", hint_force="oops")
        assert rec2 == "auto"
    asyncio.run(_run())


def test_estimator_exception_degrades_to_auto():
    """外部查询异常 → 降级 auto，不阻断任务提交。"""
    est = _make_estimator(raise_on_count=True)
    async def _run():
        rec = await est.estimate("短指令", book_id="book_b1")
        assert rec == "auto"
    asyncio.run(_run())


# ══════════ 附加：GlobalRouter 集成（四类场景路由行为） ══════════

def test_global_router_load_estimator_integration():
    """路由集成：短指令+高卡片→引擎force_split多段；短指令无资源→旧闭包单节点；
    长指令无资源→引擎auto；options 透传 no_split 覆盖。"""
    async def _run():
        from core.database import DatabaseManager
        from core.task_manager import TaskManager as CoreTaskManager
        from services.global_router import GlobalRouter
        from services.priority_queue import PriorityTaskQueue
        from services.segment_pipeline import SegmentPipeline
        from services.result_merger import ResultMerger
        from services.temp_file_manager import TempFileManager
        from services.task_manager import TaskManager

        db = DatabaseManager(db_path=TMP_ROOT / "db_router2.db")
        await db.initialize()
        temp_mgr = TempFileManager(temp_root=TMP_ROOT / "temp_router2")
        tm = TaskManager(db, _splitter, SegmentPipeline(db, temp_mgr),
                         ResultMerger(), temp_mgr)
        await tm.initialize()

        class FakeMonitor:
            def get_health_report(self):
                return {"resource": {"memory_percent": 10.0}}

            def is_locked_for_quantization(self) -> bool:
                return False

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
            load_estimator=_make_estimator(card_counts={"book_b1": 12}),
        )

        # 场景A：短指令 + 12卡片 → 识别【短指令-长内容】→ 引擎 + force_split + 多段
        req_a = CommandRequest(command="请整理这本书的世界观设定",
                               options={"book_id": "book_b1", "is_command_mode": True})
        resp_a = await router.route_command(req_a)
        assert resp_a["engine"] == "batch1", "短指令高负载应进引擎"
        assert resp_a["segment_strategy"] == "force_split"
        task_a = tm._tasks[resp_a["task_id"]]
        assert len(task_a.segments) >= 2

        # 场景B：短指令无外部资源 + rapid 模式 → 推荐 no_split → 短任务走旧闭包（单节点）
        req_b = CommandRequest(command="写一句话", options={
            "mode": "rapid", "is_command_mode": True})
        resp_b = await router.route_command(req_b)
        assert resp_b.get("engine") is None  # 旧闭包路径
        assert resp_b["task_id"].startswith("create_")

        # 场景C：长指令无外部资源 → auto → 引擎 + auto（按旧阈值拆分）
        req_c = CommandRequest(command="环境描写细节。" * 300,
                               options={"is_command_mode": True, "mode": "rapid"})
        resp_c = await router.route_command(req_c)
        assert resp_c["engine"] == "batch1"
        assert resp_c["segment_strategy"] == "auto"
        task_c = tm._tasks[resp_c["task_id"]]
        assert len(task_c.segments) >= 2

        # 场景D：options 透传 no_split 覆盖高卡片预估 → 单段
        req_d = CommandRequest(command="请整理这本书的世界观设定", options={
            "book_id": "book_b1", "is_command_mode": True,
            "segment_strategy": "no_split"})
        resp_d = await router.route_command(req_d)
        assert resp_d["segment_strategy"] == "no_split"
        task_d = tm._tasks[resp_d["task_id"]]
        assert len(task_d.segments) == 1
        await db.close()
    asyncio.run(_run())


# ══════════ 附加：业务模块 hint 参数化 ══════════

def test_business_modules_hint_param():
    """BookQuantifier / DocumentLearningEngine：默认 force_split，可被调用方覆盖。"""
    async def _run():
        from core.database import DatabaseManager
        from core.task_manager import TaskManager as CoreTaskManager
        from services.quantifier import BookQuantifier
        from services.learning_engine import DocumentLearningEngine

        db = DatabaseManager(db_path=TMP_ROOT / "db_biz.db")
        await db.initialize()
        core_tm = CoreTaskManager(db)

        class _Stub:
            pass

        class FakeDoc:
            project_id = "proj_1"
            raw_content = "x" * 100

        class FakePM:
            async def get_document(self, doc_id):
                return FakeDoc()

        quant = BookQuantifier(db=db, task_manager=core_tm, indexer=_Stub(),
                               registry=_Stub(), strategy=_Stub())
        t1 = await quant.submit_quantize_task("book_001")
        row1 = await db.get_task(t1)
        assert row1["segment_strategy"] == "force_split"
        t2 = await quant.submit_quantize_task("book_002", segment_strategy="no_split")
        row2 = await db.get_task(t2)
        assert row2["segment_strategy"] == "no_split"

        engine = DocumentLearningEngine(db=db, task_manager=core_tm,
                                        project_manager=FakePM(),
                                        dispatcher=_Stub())
        t3 = await engine.submit_learning_task("doc_001")
        row3 = await db.get_task(t3)
        assert row3["segment_strategy"] == "force_split"
        await db.close()
    asyncio.run(_run())


if __name__ == "__main__":
    import traceback

    tests = [
        test_short_command_low_external_recommends_no_split,
        test_short_command_high_external_recommends_force_split,
        test_long_command_high_external_recommends_force_split,
        test_long_command_low_external_recommends_auto,
        test_disable_estimator_falls_back_auto,
        test_hint_force_overrides_estimation,
        test_estimator_exception_degrades_to_auto,
        test_global_router_load_estimator_integration,
        test_business_modules_hint_param,
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
