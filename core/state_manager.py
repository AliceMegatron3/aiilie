"""
core/state_manager.py — 全局系统状态机
===================================================
维护系统整体调度状态，使用 asyncio.Lock 保证跨协程调用时的并发安全，
对外提供原子的状态跃迁能力。
"""
from __future__ import annotations

import asyncio
import logging
from models.system import SystemState

logger = logging.getLogger(__name__)


class StateManager:
    """系统级状态管理单例实现"""
    
    def __init__(self) -> None:
        self._state = SystemState.IDLE
        self._lock = asyncio.Lock()
        self._context_locked_project_id: str | None = None
        
    async def get_state(self) -> SystemState:
        """安全读取当前系统状态"""
        async with self._lock:
            return self._state
            
    async def set_state(self, new_state: SystemState) -> None:
        """强制写入状态（常用于从 ERROR 恢复 或 强行 IDLE）"""
        async with self._lock:
            if self._state != new_state:
                logger.info("[StateManager] 状态跃迁: %s -> %s", self._state.value, new_state.value)
                self._state = new_state
                
    async def try_acquire_run(self, intended_state: SystemState) -> bool:
        """
        尝试获取系统独占执行权，仅在系统 IDLE 或 ERROR 时可跃迁为执行态。
        返回 True 表示获取成功，False 表示系统当前忙碌，任务应继续排队。
        """
        async with self._lock:
            if self._state not in (SystemState.IDLE, SystemState.ERROR):
                logger.warning(
                    "[StateManager] 独占执行权获取失败：试图跃迁为 %s，但当前系统正忙于 %s",
                    intended_state.value, self._state.value
                )
                return False
                
            logger.info("[StateManager] 成功夺取执行权: %s -> %s", self._state.value, intended_state.value)
            self._state = intended_state
            return True
            
    async def release_to_idle(self) -> None:
        """任务执行完毕，释放系统控制权回到空闲态，准备接待队列下一个任务。"""
        async with self._lock:
            if self._state != SystemState.IDLE:
                logger.info("[StateManager] 释放系统控制权: %s -> %s", self._state.value, SystemState.IDLE.value)
                self._state = SystemState.IDLE

    async def get_context_lock(self) -> str | None:
        """获取当前全局锁定的子项目上下文 ID"""
        async with self._lock:
            return self._context_locked_project_id

    async def set_context_lock(self, project_id: str | None) -> None:
        """设置当前全局锁定的子项目上下文 ID"""
        async with self._lock:
            if self._context_locked_project_id != project_id:
                logger.info("[StateManager] 智能体上下文切换: %s -> %s", self._context_locked_project_id, project_id)
                self._context_locked_project_id = project_id
