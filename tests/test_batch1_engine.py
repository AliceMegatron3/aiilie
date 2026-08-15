"""
tests/test_batch1_engine.py — 批次1分段执行引擎修复验证脚本
============================================================
独立可运行（python tests/test_batch1_engine.py），也可被 pytest 收集。

覆盖验证点：
  P0-1  SegmentPipeline DB 阈值外置：大 result_content 不入 SQLite，只存 output_path
  P0-2  ResultMerger 流式合并：合并产物落盘文件，merge_and_load 阈值兼容模式
  P0-3  GlobalRouter 长任务改走批次1引擎（engine=batch1），开关可回退旧闭包
  P1-4  TailContextManager：大尾巴落盘标记 / materialize / 失败现场快照 / 重启恢复
  P2-5  task_api 路由：任务详情 / 分段列表 / 取消 / 审计报告（走 TaskManager 封装）
  P2-6  TempFileManager：启动孤儿清理（保护运行中任务）+ LRU 磁盘配额淘汰
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 保证直接运行脚本时项目根在 sys.path 上
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TMP_ROOT = Path(tempfile.mkdtemp(prefix="batch1_smoke_"))
# 重定向 APPDATA，所有持久化/结果目录均落在临时沙箱内
os.environ["APPDATA"] = str(TMP_ROOT / "appdata")

from core.database import DatabaseManager  # noqa: E402
from core.config_manager import config_manager  # noqa: E402
from models.system import CommandRequest, PriorityLevel  # noqa: E402
from models.task import SegmentStatus, TaskStatus  # noqa: E402
from services.command_splitter import CommandSplitter  # noqa: E402
from services.priority_queue import PriorityTaskQueue  # noqa: E402
from services.result_merger import (  # noqa: E402
    ResultMerger,
    resolve_segment_content,
)
from services.segment_pipeline import SegmentPipeline  # noqa: E402
from services.tail_context_manager import (  # noqa: E402
    OFFLOAD_MARKER_KEY,
    TailContextManager,
)
from services.task_manager import TaskManager  # noqa: E402
from services.temp_file_manager import TempFileManager  # noqa: E402

# 缩小阈值便于测试触发
DB_THRESHOLD = 500      # result_content 超过 500 字节即不入库
TAIL_THRESHOLD = 300    # 尾巴超过 300 字节即落盘


# ── 执行钩子（模拟批次3注入的真实业务） ─────────────────────────
async def small_hook(content, tail, source):
    processed = tail.get("processed_count", 0) + 1
    new_tail = {**tail, "processed_count": processed}
    return f"OUT[{processed}]:{content[:30]}", new_tail


async def big_hook(content, tail, source):
    processed = tail.get("processed_count", 0) + 1
    new_tail = {**tail, "processed_count": processed, "big_blob": "T" * 1000}
    result = ("R" * 2000) + f"\nSEG_{processed}\n"
    return result, new_tail


FAIL_FLAG = {"on": True}


async def flaky_hook(content, tail, source):
    """第2段模拟网络中断，用于验证失败现场快照与重启恢复。"""
    processed = tail.get("processed_count", 0) + 1
    new_tail = {**tail, "processed_count": processed}
    if FAIL_FLAG["on"] and processed == 2:
        raise RuntimeError("模拟网络中断")
    return f"FLK[{processed}]", new_tail


def _make_engine(db, tail_root, temp_root, hook, db_threshold=DB_THRESHOLD,
                 tail_threshold=TAIL_THRESHOLD):
    temp_mgr = TempFileManager(temp_root=temp_root, max_size_bytes=None)
    tail_mgr = TailContextManager(
        temp_root=tail_root,
        memory_threshold_bytes=tail_threshold,
        enable_disk_offload=True,
    )
    pipeline = SegmentPipeline(
        db, temp_mgr, execution_hook=hook, tail_manager=tail_mgr,
        max_db_payload_bytes=db_threshold,
    )
    merger = ResultMerger()
    tm = TaskManager(
        db, CommandSplitter(), pipeline, merger, temp_mgr,
        tail_manager=tail_mgr,
    )
    return tm, temp_mgr, tail_mgr, merger


async def _db_segment(db, segment_id):
    return await db.get_segment(segment_id)


async def main() -> None:
    db_path = TMP_ROOT / "tasks.db"
    db = DatabaseManager(db_path=db_path)
    await db.initialize()

    tail_root = TMP_ROOT / "tails"
    temp_root = TMP_ROOT / "temp"
    tail_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)

    # ══════════ P0-1：DB 阈值外置（小结果入库） ══════════
    tm, temp_mgr, tail_mgr, merger = _make_engine(db, tail_root, temp_root, small_hook)
    await tm.initialize()
    cmd_small = "环境描写细节。" * 300  # ~2100字，by_length拆2段
    task_a = await tm.submit_task(cmd_small)
    assert len(task_a.segments) >= 2, "长文本应被拆分"
    result_a = await tm.process_next()
    assert result_a.status == TaskStatus.COMPLETED, f"任务A未完成: {result_a.error_message}"
    for seg in task_a.segments:
        row = await _db_segment(db, seg.segment_id)
        assert row["result_content"] is not None, "短结果应入库"
        assert "OUT[" in row["result_content"]
    merged_path = merger._output_dir / f"{task_a.task_id}_result.txt"
    assert merged_path.exists(), "合并结果文件应存在"
    merged_text = merged_path.read_text(encoding="utf-8")
    assert "OUT[1]" in merged_text and "OUT[2]" in merged_text
    print("[P0-1] 短结果入库 + 流式合并文件 ✔")

    # ══════════ P0-1+P0-2：大结果不入库 + 流式合并 ══════════
    # 说明：process_next 收尾会清理分段临时目录（结果已合并进 results 目录），
    # 因此"output_path 文件存在"检查放在手动执行（合并之前）进行。
    tm2, temp_mgr2, tail_mgr2, merger2 = _make_engine(db, tail_root, temp_root, big_hook)
    await tm2.initialize()
    cmd_big = "剧情推进发展。" * 300
    task_b = await tm2.submit_task(cmd_big)
    seg_count = len(task_b.segments)
    executed_b = await tm2._pipeline.execute_all(task_b.segments)
    task_b.segments = executed_b
    assert all(s.status == SegmentStatus.COMPLETED for s in executed_b)
    for seg in executed_b:
        row = await _db_segment(db, seg.segment_id)
        assert row["result_content"] is None, "大结果不应写入 SQLite"
        assert row["output_path"], "大结果必须持久化 output_path"
        out_file = Path(row["output_path"])
        assert out_file.exists(), f"输出文件应存在: {out_file}"
        assert out_file.stat().st_size > 2000
        # 磁盘优先读取语义
        content = resolve_segment_content(seg)
        assert content is not None and "SEG_" in content
    # 流式合并（产物落盘，不占内存）
    merged_b = await merger2.merge(task_b)
    assert merged_b and Path(merged_b).exists()
    size_b = Path(merged_b).stat().st_size
    assert size_b > seg_count * 2000, f"合并体积异常: {size_b}"
    # merge_and_load 阈值兼容模式
    small_str = await merger2.merge_and_load(task_b, max_bytes=100)
    assert small_str is None, "超阈值应返回 None"
    big_str = await merger2.merge_and_load(task_b, max_bytes=size_b + 100)
    assert isinstance(big_str, str) and "SEG_1" in big_str
    # 手动执行链路收尾：任务B实际已全部执行完，落库标记完成，
    # 避免其 PENDING 状态污染后续 tm3 的任务恢复队列
    await db.update_task_status(task_b.task_id, "COMPLETED")
    print("[P0-2] 大结果不入库 / 流式合并 / merge_and_load 阈值 ✔")

    # ══════════ P1-4：大尾巴落盘标记 + materialize ══════════
    last_seg = sorted(task_b.segments, key=lambda s: s.sequence_order)[-1]
    row_last = await _db_segment(db, last_seg.segment_id)
    import json as _json
    tail_ref = _json.loads(row_last["tail_context"])
    assert OFFLOAD_MARKER_KEY in tail_ref, "大尾巴应落盘为标记 dict"
    materialized = tail_mgr2.materialize(tail_ref)
    assert materialized.get("big_blob") == "T" * 1000
    assert materialized["processed_count"] >= 1
    offload_file = Path(tail_ref[OFFLOAD_MARKER_KEY]["path"])
    assert offload_file.exists(), "尾巴落盘文件应存在"
    print("[P1-4] 大尾巴双模式落盘 + materialize ✔")

    # ══════════ P1-4：失败现场快照 + 重启恢复 ══════════
    tm3, temp_mgr3, tail_mgr3, merger3 = _make_engine(db, tail_root, temp_root, flaky_hook)
    await tm3.initialize()
    cmd_c = "冲突爆发描写。" * 300
    task_c = await tm3.submit_task(cmd_c)
    segs_c = sorted(task_c.segments, key=lambda s: s.sequence_order)
    failed_seg = segs_c[1]
    result_c = await tm3.process_next()
    assert result_c.status == TaskStatus.FAILED, "任务C应因注入异常失败"
    snapshot = tail_mgr3.load_failure_snapshot(
        task_c.task_id, failed_seg.segment_id
    )
    assert snapshot is not None and snapshot.get("processed_count") == 1, \
        "失败现场快照应保存失败的尾巴"
    # 模拟重启：新引擎实例从 DB 恢复（PENDING/RUNNING→PENDING），注入快照续跑。
    # 关键：只恢复失败分段（seg1），隔离验证快照注入机制——
    # 若无快照，seg1 的输入尾巴为空，processed_count 应为 1；
    # 注入快照后应为 2。
    FAIL_FLAG["on"] = False
    await db.update_task_status(task_c.task_id, "PENDING")
    await db.update_segment_status(failed_seg.segment_id, "PENDING")
    tm3b, _, _, _ = _make_engine(db, tail_root, temp_root, flaky_hook)
    await tm3b.initialize()
    recovered_task = tm3b._tasks[task_c.task_id]
    recovered_task.segments = [
        s for s in recovered_task.segments
        if s.segment_id == failed_seg.segment_id
    ]
    recovered = await tm3b.process_next()
    assert recovered.status == TaskStatus.COMPLETED, \
        f"重启恢复应成功: {recovered.error_message}"
    row_failed = await _db_segment(db, failed_seg.segment_id)
    tail_after = _json.loads(row_failed["tail_context"])
    mat = tail_mgr3.materialize(tail_after)
    assert mat.get("processed_count") == 2, \
        "恢复后的分段应继承快照尾巴(processed_count=2)"
    assert tail_mgr3.load_failure_snapshot(
        task_c.task_id, failed_seg.segment_id
    ) is None, "恢复成功后快照应被清除"
    print("[P1-4] 失败现场快照 + 重启恢复链路 ✔")

    # ══════════ P2-6：启动孤儿清理 + 保护 + LRU 配额 ══════════
    orphan_dir = temp_root / "seg_orphan_abc"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "leftover.txt").write_text("x" * 100, encoding="utf-8")
    keep_dir = temp_root / "seg_keep_run"
    keep_dir.mkdir(parents=True, exist_ok=True)
    (keep_dir / "running.txt").write_text("y" * 100, encoding="utf-8")
    tm_clean, temp_mgr4, _, _ = _make_engine(db, tail_root, temp_root, small_hook)
    removed = temp_mgr4.startup_cleanup(protected_segment_ids={"seg_keep_run"})
    assert not orphan_dir.exists(), "孤儿目录应被清理"
    assert keep_dir.exists(), "受保护目录禁止删除"
    assert removed >= 1
    # LRU：配额 1000 字节，两个目录各 800 字节 → 淘汰最旧
    lru_root = TMP_ROOT / "lru_temp"
    lru_root.mkdir(parents=True, exist_ok=True)
    lru_mgr = TempFileManager(temp_root=lru_root, max_size_bytes=1000)
    d1 = lru_mgr.get_segment_dir("seg_lru_a")
    (d1 / "f.txt").write_text("A" * 800, encoding="utf-8")
    d2 = lru_mgr.get_segment_dir("seg_lru_b")
    (d2 / "f.txt").write_text("B" * 800, encoding="utf-8")
    lru_mgr._enforce_quota()
    assert not d1.exists(), "最久未访问的目录应被 LRU 淘汰"
    assert d2.exists(), "最近访问的目录应保留"
    print("[P2-6] 启动孤儿清理(保护) + LRU 磁盘配额 ✔")

    # ══════════ P2-5：task_api 独立路由（同循环直接调用处理函数） ══════════
    # 说明：TestClient 在独立线程事件循环运行，aiosqlite 连接跨 loop 会报错，
    # 因此引擎级验证直接调用路由处理函数（与 HTTP 层同一段代码路径）。
    from api.task_api import (
        cancel_task as api_cancel,
        get_task_audit_report as api_audit,
        get_task_detail as api_detail,
        get_task_segments as api_segments,
    )
    from fastapi import HTTPException

    tm_api, _, _, _ = _make_engine(db, tail_root, temp_root, small_hook)
    await tm_api.initialize()
    task_d = await tm_api.submit_task("短任务：写一句话")
    await tm_api.process_next()

    detail = await api_detail(task_d.task_id, tm_api)
    assert detail["status"] == "COMPLETED"
    assert detail["progress"]["completed"] >= 1
    segs = await api_segments(task_d.task_id, tm_api)
    assert segs["total"] >= 1
    assert segs["segments"][0]["result_available"] is True
    rep = await api_audit(task_d.task_id, tm_api)
    assert "success_rate" in rep
    try:
        await api_detail("task_nonexistent_123", tm_api)
        raise AssertionError("不存在的任务应返回404")
    except HTTPException as e:
        assert e.status_code == 404
    # 取消：新建排队任务不消费，直接取消
    task_e = await tm_api.submit_task("待取消任务")
    res = await api_cancel(task_e.task_id, tm_api)
    assert res["cancelled"] is True
    try:
        await api_audit("task_nonexistent_123", tm_api)
        raise AssertionError("不存在的任务应返回404")
    except HTTPException as e:
        assert e.status_code == 404
    print("[P2-5] task_api 四接口（详情/分段/取消/审计） ✔")

    # ══════════ P0-3：GlobalRouter 集成批次1引擎 ══════════
    from core.task_manager import TaskManager as CoreTaskManager
    from services.global_router import GlobalRouter

    class FakeMonitor:
        def get_health_report(self):
            return {"resource": {"memory_percent": 10.0}}

    class FakeApplier:
        async def fetch_active_rules(self, scope=None, context_features=None):
            return []

    class FakeTrigger:
        pass

    core_tm = CoreTaskManager(db)
    router = GlobalRouter(
        task_queue=PriorityTaskQueue(),
        system_monitor=FakeMonitor(),
        task_manager=core_tm,
        reflection_trigger=FakeTrigger(),
        optimization_applier=FakeApplier(),
        model_dispatcher=None,
        project_manager=None,
        batch1_task_manager=tm_api,
    )
    long_cmd = ("请为本书创作开篇内容，注意环境描写。" * 150)  # ~2550字
    req = CommandRequest(command=long_cmd, options={"mode": "think", "is_command_mode": True})
    resp = await router.route_command(req)
    assert resp.get("engine") == "batch1", f"长任务应走批次1引擎: {resp}"
    assert resp["task_id"].startswith("task_")
    assert resp["segment_count"] >= 1
    # 提交的任务真实进入批次1管理器，并可由引擎消费完成
    # （队列中可能混有此前已取消的任务，循环消费直至路由任务到达终态）
    routed_task = tm_api._tasks.get(resp["task_id"])
    assert routed_task is not None
    for _ in range(5):
        if routed_task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            break
        await tm_api.process_next()
    assert routed_task.status == TaskStatus.COMPLETED, \
        f"路由任务应完成: {routed_task.error_message}"
    # 开关回退：关闭引擎后切回旧闭包队列（task_id 前缀 create_，无 engine 字段）
    config_manager._config.setdefault("global_router", {})["use_batch1_engine"] = False
    resp_old = await router.route_command(req)
    assert resp_old.get("engine") is None
    assert resp_old["task_id"].startswith("create_")
    config_manager._config["global_router"]["use_batch1_engine"] = True
    # 短任务（rapid、非深度模式、短文本）走旧闭包
    req_short = CommandRequest(command="写一句话", options={"mode": "rapid", "is_command_mode": True})
    resp_short = await router.route_command(req_short)
    assert resp_short.get("engine") is None and resp_short["task_id"].startswith("create_")
    print("[P0-3] GlobalRouter→批次1引擎集成 + 开关回退 ✔")

    # 收尾
    await db.close()
    shutil.rmtree(str(TMP_ROOT), ignore_errors=True)
    print("\n========== 批次1引擎修复验证全部通过 ==========")


if __name__ == "__main__":
    asyncio.run(main())
