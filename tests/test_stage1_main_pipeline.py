"""阶段1主链路真实化回归测试（讨论稿20260816第三章修复顺序第1步）。

覆盖三组改动：
A. SegmentPipeline 真实执行钩子（services/segment_execution_hook.py + bootstrap 装配）
B. 子智能体创作角色 prompt（novel_role_* 模板）与 rag_filters 消费（dispatcher.card_filters）
C. 子智能体输出汇合（串行精修，返回真实正文而非汇总字符串）

配套：batch1 submit_task 的 project_id 注入（项目绑定随尾巴接力进入执行钩子）。
"""
from __future__ import annotations

import json

import pytest

from core.database import DatabaseManager
from models.task import Segment, SegmentStatus
from services.command_splitter import CommandSplitter
from services.result_merger import ResultMerger
from services.segment_execution_hook import build_model_execution_hook
from services.segment_pipeline import SegmentPipeline
from services.task_manager import TaskManager as Batch1TaskManager
from services.temp_file_manager import TempFileManager


class _RecordingDispatcher:
    """记录调用参数的假调度器，按序吐出预置输出。"""

    def __init__(self, outputs: list[str] | None = None):
        self.outputs = list(outputs or [])
        self.calls: list[dict] = []

    async def dispatch(
        self,
        prompt: str,
        project_id: str | None = None,
        override_mode: str | None = None,
        session_id: str | None = None,
        card_filters: dict | None = None,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "project_id": project_id,
                "override_mode": override_mode,
                "card_filters": card_filters,
            }
        )
        return self.outputs.pop(0) if self.outputs else "正文占位"


# ──────────────────────────────────────────────────────────────
# A. 真实执行钩子契约
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_model_hook_returns_json_envelope_with_real_text():
    dispatcher = _RecordingDispatcher(outputs=["长安城破的当夜，火光映红了渭水。"])
    hook = build_model_execution_hook(lambda: dispatcher)

    raw = await hook("写一段城破场景", {"project_id": "proj-1"}, "cloud")
    envelope = json.loads(raw)

    assert envelope["result_content"] == "长安城破的当夜，火光映红了渭水。"
    assert "[PLACEHOLDER]" not in envelope["result_content"]
    assert envelope["new_tail"]["processed_count"] == 1
    # project_id 透传给 dispatcher（卡片上下文加载的钥匙）
    assert dispatcher.calls[0]["project_id"] == "proj-1"
    # 云端/混合来源映射为 think（云端优先+本地兜底）
    assert dispatcher.calls[0]["override_mode"] == "think"


@pytest.mark.asyncio
async def test_model_hook_maps_local_source_to_default_mode():
    dispatcher = _RecordingDispatcher(outputs=["本地模型正文。"])
    hook = build_model_execution_hook(lambda: dispatcher)

    await hook("内容", {}, "local")
    assert dispatcher.calls[0]["override_mode"] is None


@pytest.mark.asyncio
async def test_model_hook_refuses_missing_dispatcher_and_empty_output():
    hook = build_model_execution_hook(lambda: None)
    with pytest.raises(RuntimeError):
        await hook("内容", {}, "cloud")

    dispatcher = _RecordingDispatcher(outputs=["   "])
    hook2 = build_model_execution_hook(lambda: dispatcher)
    with pytest.raises(ValueError):
        await hook2("内容", {}, "cloud")


# ──────────────────────────────────────────────────────────────
# A2. 流水线接真实钩子后产出真实正文，且 project_id 沿尾巴接力
# ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_with_model_hook_produces_real_text(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "stage1-pipeline.db")
    await db.initialize()
    temp_manager = TempFileManager(temp_root=tmp_path / "temp")
    dispatcher = _RecordingDispatcher(
        outputs=["第一段：大军压境。", "第二段：城中人心惶惶。"]
    )
    pipeline = SegmentPipeline(
        db,
        temp_manager,
        execution_hook=build_model_execution_hook(lambda: dispatcher),
    )
    try:
        seg1 = Segment(
            parent_task_id="task-stage1",
            content_payload="写第一段",
            sequence_order=0,
            tail_context={"project_id": "proj-1"},
        )
        seg2 = Segment(
            parent_task_id="task-stage1",
            content_payload="写第二段",
            sequence_order=1,
        )
        executed = await pipeline.execute_all([seg1, seg2], "cloud")

        assert all(s.status == SegmentStatus.COMPLETED for s in executed)
        assert executed[0].result_content == "第一段：大军压境。"
        assert executed[1].result_content == "第二段：城中人心惶惶。"
        assert all("[PLACEHOLDER]" not in (s.result_content or "") for s in executed)
        # 首段携带的 project_id 经 new_tail 接力传给第二段的钩子
        assert dispatcher.calls[0]["project_id"] == "proj-1"
        assert dispatcher.calls[1]["project_id"] == "proj-1"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_batch1_submit_task_injects_project_into_first_segment(tmp_path):
    db = DatabaseManager(db_path=tmp_path / "stage1-submit.db")
    await db.initialize()
    temp_manager = TempFileManager(temp_root=tmp_path / "temp")

    async def hook(content, tail, source, retry_prompt=None):
        return content, tail

    manager = Batch1TaskManager(
        db,
        CommandSplitter(),
        SegmentPipeline(db, temp_manager, execution_hook=hook),
        ResultMerger(),
        temp_manager,
    )
    try:
        task = await manager.submit_task(
            "写一个完整章节的城破与逃亡", project_id="proj-9"
        )
        assert task.segments, "拆分器应产出至少一个分段"
        assert task.segments[0].tail_context.get("project_id") == "proj-9"
        # 未传项目时不注入
        task2 = await manager.submit_task("再来一段")
        assert "project_id" not in (task2.segments[0].tail_context or {})
        # pipeline 属性可访问（bootstrap 钩子注入口）
        assert manager.pipeline is not None
    finally:
        await db.close()


# ──────────────────────────────────────────────────────────────
# B+C. 子智能体：角色创作 prompt、rag_filters 消费、输出汇合
# ──────────────────────────────────────────────────────────────

def _make_supervisor(dispatcher):
    from services.novel_supervisor import NovelSupervisor

    return NovelSupervisor(indexer=None, optimization_applier=None, dispatcher=dispatcher)


@pytest.mark.asyncio
async def test_supervisor_uses_role_prompts_and_merges_output():
    dispatcher = _RecordingDispatcher(
        outputs=[
            "【设定专家稿】初冬的洛阳米价飞涨。",
            "【动作专家稿】乱兵冲入坊市，刃光起落。",
            "【情感专家稿】少女攥紧了半袋糙米。",
            "【情节专家稿】火起三坊，夜遁北门。",
        ]
    )
    supervisor = _make_supervisor(dispatcher)

    result = await supervisor.execute_creation(
        task_id="task-s1", cmd_text="写一段乱世坊市场景", project_id="proj-1"
    )

    assert result["success"] is True
    # 输出汇合：result 是最终正文而非汇总字符串
    assert result["result"] == "【情节专家稿】火起三坊，夜遁北门。"
    assert "多智能体创作完成" in result["summary"]
    assert result["result"] != result["summary"]

    # 每个角色拿到创作导向 prompt（含角色身份），而非原始指令直通
    prompts = [c["prompt"] for c in dispatcher.calls]
    assert len(prompts) >= 4
    assert "设定" in prompts[0] and "任务与素材" in prompts[0]
    assert prompts[0] != "写一段乱世坊市场景"
    # 串行精修：后续角色的 prompt 携带前序草稿
    assert "当前草稿" in prompts[1]
    assert "【设定专家稿】" in prompts[1]


@pytest.mark.asyncio
async def test_supervisor_without_dispatcher_falls_back_to_echo():
    supervisor = _make_supervisor(None)
    result = await supervisor.execute_creation(
        task_id="task-s2", cmd_text="测试指令"
    )
    assert result["success"] is True
    assert result["result"]  # 直通模式下仍有非空返回


def test_merge_rag_filters_dedupes_and_overrides():
    from services.novel_supervisor import NovelSupervisor

    merged = NovelSupervisor._merge_rag_filters(
        [{"knowledge_type": "FACT"}, {"domain": "history", "knowledge_type": "RULE"}]
    )
    assert merged == {"knowledge_type": "RULE", "domain": "history"}
    assert NovelSupervisor._merge_rag_filters([]) is None
    assert NovelSupervisor._merge_rag_filters(["bad", None]) is None


def test_creation_role_templates_are_builtin_valid_json():
    """四个创作角色模板落盘且契约完整（variables 含 content）。"""
    from pathlib import Path

    from utils.resource_path import get_resource_path

    builtin_dir = Path(get_resource_path("data/prompts/builtin"))
    for role in ("lore", "combat", "emotion", "event"):
        path = builtin_dir / f"novel_role_{role}.json"
        assert path.exists(), f"缺少创作模板 {path.name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["template_id"] == f"novel_role_{role}"
        assert data["category"] == "creation"
        assert "content" in data["variables"]
        assert "{{ content }}" in data["template_text"]
