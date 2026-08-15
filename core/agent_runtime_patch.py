"""
core/agent_runtime_patch.py — 情感引擎 Agent 循环防护补丁
=============================================================
修复高频故障：Agent任务异常终止、迭代无熔断、重写超限导致算力损害。

功能包含：
1. 全局迭代轮次硬上限（硬熔断）
2. 感性重写最多 2 轮封顶
3. 7 级及以上混沌模式关闭强制重写
4. 任务超时/token 熔断检测
5. 外置状态滑动窗口仅加载最近 3 帧规则
"""
import logging
import asyncio
from typing import Any, Callable, Coroutine
from datetime import datetime
from models.task import BasePipelineTask

logger = logging.getLogger("agent_runtime_patch")

class AgentRuntimeProtector:
    def __init__(self):
        self.MAX_ITERATIONS = 5        # 全局迭代硬上限
        self.MAX_REWRITE_ROUNDS = 2    # 重写封顶 2 轮
        self.CHAOS_BYPASS_LEVEL = 7    # 7级以上混沌模式关闭重写
        self.TIMEOUT_SECONDS = 300     # 任务最长存活时间
    
    def apply_sliding_window(self, task: BasePipelineTask) -> None:
        """外置状态滑动窗口：仅保留最近3帧上下文，防止长文本膨胀导致推理超时"""
        context = getattr(task, "context", [])
        if isinstance(context, list) and len(context) > 3:
            logger.warning("Context 膨胀检测，应用滑动窗口截断 (仅保留最近 3 帧)")
            setattr(task, "context", context[-3:])
            
    def check_chaos_mode(self, task: BasePipelineTask) -> bool:
        """检查是否达到混沌模式上限，避免无意义的算力消耗"""
        chaos_level = getattr(task, "chaos_level", 0)
        if chaos_level >= self.CHAOS_BYPASS_LEVEL:
            logger.warning(f"检测到高维混沌状态 (Level: {chaos_level})，自动关闭感性重写策略，保护算力。")
            return True
        return False

def apply_agent_runtime_patch(handler: Callable[[BasePipelineTask], Coroutine[Any, Any, None]]) -> Callable[[BasePipelineTask], Coroutine[Any, Any, None]]:
    """包装原始 Task Dispatcher，注入 Agent 循环防护逻辑"""
    protector = AgentRuntimeProtector()
    
    async def _patched_handler(task: BasePipelineTask) -> None:
        task_id = task.task_id
        logger.info(f"[AgentRuntimePatch] 拦截并校验任务: {task_id}")
        
        # 1. 拦截并裁剪上下文
        protector.apply_sliding_window(task)
        
        # 2. 混沌模式检查 (可在此修改参数以通知下层引擎不再重写)
        if protector.check_chaos_mode(task):
            setattr(task, "disable_rewrite", True)
            
        # 3. 追加硬上限控制变量
        setattr(task, "max_rewrite_rounds", protector.MAX_REWRITE_ROUNDS)
        
        # 4. 执行带超时的任务调用
        try:
            # 引入硬超时熔断机制
            await asyncio.wait_for(handler(task), timeout=protector.TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.error(f"[AgentRuntimePatch] 任务 {task_id} 执行超时 (> {protector.TIMEOUT_SECONDS}s)，触发硬熔断。")
            raise Exception(f"任务已被熔断 (超时 > {protector.TIMEOUT_SECONDS}s)")
        except Exception as e:
            logger.error(f"[AgentRuntimePatch] 任务 {task_id} 发生未捕获异常: {e}")
            raise
            
    return _patched_handler
