"""core/startup_stages.py — 启动阶段注册表(结构债2)
=====================================================
此前 initialize_app 把「阶段顺序、fail-fast 语义、可选收尾动作」全部
硬编码在一个函数里:读的人要靠注释才知道哪步能失败、哪步不能。

本模块把这些提为**显式声明**:每个阶段是一条 StartupStage 记录,
带名称、执行体、是否 fail-fast、依赖说明。initialize_app 退化为
"遍历阶段表"的十行循环。

为什么不把 setup_* 实现也搬过来:它们各自依赖 bootstrap 内的
局部装配上下文,整体搬迁属高风险重构且收益仅是行数搬移;
本次只治理"顺序与失败语义不可见"这一真实痛点。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

StageFn = Callable[[Any], Awaitable[None]]


@dataclass(frozen=True)
class StartupStage:
    """一个启动阶段的声明。

    critical=True  → 失败即中断启动(fail fast),核心底座;
    critical=False → 失败仅告警继续(增强项/自检/清理)。
    """
    name: str
    run: StageFn
    critical: bool = True
    depends_on: tuple[str, ...] = field(default=())
    note: str = ""


async def run_stages(app: Any, stages: list[StartupStage]) -> list[str]:
    """按声明顺序执行阶段;返回非致命失败的阶段名列表。"""
    degraded: list[str] = []
    for stage in stages:
        try:
            await stage.run(app)
            logger.info("[Startup] 阶段完成: %s", stage.name)
        except Exception as exc:
            if stage.critical:
                logger.error("[Startup] 关键阶段失败,中断启动: %s (%s)", stage.name, exc)
                raise
            degraded.append(stage.name)
            logger.warning(
                "[Startup] 增强阶段失败(降级继续): %s (%s)", stage.name, exc
            )
    return degraded


def validate_stage_order(stages: list[StartupStage]) -> list[str]:
    """校验依赖声明:被依赖阶段必须排在前面。返回问题描述列表。"""
    problems: list[str] = []
    seen: set[str] = set()
    for stage in stages:
        for dep in stage.depends_on:
            if dep not in seen:
                problems.append(f"阶段 {stage.name} 依赖 {dep},但后者未在其之前装配")
        seen.add(stage.name)
    return problems
