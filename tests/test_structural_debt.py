"""结构债回归:语义命名/启动阶段表/分层依赖。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


# ── 债1:双 TaskManager 语义命名与兼容别名 ──────────────────────

def test_semantic_names_and_back_compat_aliases():
    from core.task_manager import PersistentTaskQueue, TaskManager as CoreTM
    from services.task_manager import TaskManager as SvcTM, TaskOrchestrator

    # 语义名与旧名指向同一类(零破坏)
    assert CoreTM is PersistentTaskQueue
    assert TaskOrchestrator is SvcTM
    # 两者仍是不同机制,不应被合并
    assert PersistentTaskQueue is not TaskOrchestrator
    # 语义边界:队列版收任务对象,编排器收指令字符串
    import inspect

    queue_params = list(inspect.signature(PersistentTaskQueue.submit_task).parameters)
    orch_params = list(inspect.signature(TaskOrchestrator.submit_task).parameters)
    assert "task" in queue_params
    assert "raw_command" in orch_params


# ── 债2:启动阶段表 ────────────────────────────────────────────

def test_startup_stage_table_declares_order_and_failure_semantics():
    from core.bootstrap import build_startup_stages
    from core.startup_stages import validate_stage_order

    stages = build_startup_stages()
    names = [s.name for s in stages]
    # 关键底座在前,可选收尾在后
    assert names.index("base") < names.index("quantification")
    assert names.index("reflection") < names.index("quantification")
    assert names.index("quantification") < names.index("control_center")
    # 依赖声明自洽
    assert validate_stage_order(stages) == []
    # 失败语义显式:底座 critical,收尾非 critical
    by_name = {s.name: s for s in stages}
    assert by_name["base"].critical is True
    assert by_name["control_center"].critical is True
    assert by_name["prompt_audit"].critical is False
    assert by_name["blueprint"].critical is False
    assert by_name["orphan_cleanup"].critical is False


def test_validate_stage_order_detects_bad_declaration():
    from core.startup_stages import StartupStage, validate_stage_order

    async def noop(app):
        return None

    bad = [
        StartupStage("later", noop, depends_on=("earlier",)),
        StartupStage("earlier", noop),
    ]
    problems = validate_stage_order(bad)
    assert problems and "earlier" in problems[0]


@pytest.mark.asyncio
async def test_run_stages_fail_fast_vs_degrade():
    from core.startup_stages import StartupStage, run_stages

    calls: list[str] = []

    async def ok(app):
        calls.append("ok")

    async def boom(app):
        raise RuntimeError("阶段炸了")

    # 非关键阶段失败→降级继续
    degraded = await run_stages(object(), [
        StartupStage("soft", boom, critical=False),
        StartupStage("after", ok),
    ])
    assert degraded == ["soft"] and calls == ["ok"]

    # 关键阶段失败→中断
    with pytest.raises(RuntimeError):
        await run_stages(object(), [StartupStage("hard", boom, critical=True)])


# ── 债3:分层依赖(core 不在模块级 import services) ───────────────

def _module_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:  # 只看模块级,函数体内的延迟 import 不算违规
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_core_layer_has_no_module_level_service_imports():
    violations: dict[str, set[str]] = {}
    for py in Path("core").glob("*.py"):
        bad = {m for m in _module_level_imports(py) if m.startswith("services")}
        if bad:
            violations[py.name] = bad
    assert violations == {}, f"core 层存在模块级 services 依赖: {violations}"


def test_plugin_manager_still_renders_planner_prompt():
    """延迟 import 不得破坏功能。"""
    from core.plugin_manager import plugin_manager

    prompt = plugin_manager.render_call_plan_prompt({
        "caller_id": "tester", "caller_permission": 3,
        "task_description": "结构债回归",
    })
    assert isinstance(prompt, str) and prompt.strip()


# ── 债4:群像录入端点(数据冷启动) ───────────────────────────────

def test_ensemble_input_endpoints_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    # WebSocket 路由无 methods 属性,统一用 getattr 兜底
    paths = {
        (tuple(sorted(getattr(r, "methods", None) or ())), r.path)
        for r in flatten_api_router(api_router)
    }
    flat = {p for _, p in paths}
    assert any(p.endswith("/tracks") for p in flat)
    assert any(p.endswith("/relationships/baseline") for p in flat)
    assert any(p.endswith("/relationships/event-delta") for p in flat)
    assert any(p.endswith("/voices") for p in flat)
    # 轨道端点须同时支持读与写
    methods = {tuple(sorted(m)) for m, p in paths if p.endswith("/tracks")}
    all_methods = {x for tup in methods for x in tup}
    assert "GET" in all_methods and "POST" in all_methods
