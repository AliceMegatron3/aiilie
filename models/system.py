"""
models/system.py — 批次5全局中枢数据类型定义
===================================================
提供系统状态机枚举、优先级契约与全局指令统一入口格式。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

class SystemState(str, Enum):
    """全局系统状态机，标定中枢目前在调度哪类大规模流水线。"""
    IDLE = "IDLE"
    RUNNING_SEGMENT = "RUNNING_SEGMENT"   # 创作生成中
    RUNNING_LIBRARY = "RUNNING_LIBRARY"   # 知识库量化建库中
    RUNNING_REFLECT = "RUNNING_REFLECT"   # 深层反思进化中
    ERROR = "ERROR"                       # 核心总线挂起异常

class PriorityLevel(int, Enum):
    """
    全局调度优先级 (数字越大优先级越高)。
    配合 PriorityTaskQueue 使用，支持高优任务(反思)插队。
    """
    LV7 = 70  # 反思复盘 (最高优)
    LV6 = 60  # 重度长篇创作/海量卡片量化
    LV4 = 40  # 碎片化普通创作
    LV1 = 10  # 文档导出/资源清理等后台低优清理

class CommandRequest(BaseModel):
    """统一指令大网关请求格式。"""
    command: str = Field(..., description="用户的原始指令，如 '帮我写第一章，注重环境描写'")
    options: dict[str, Any] = Field(default_factory=dict, description="额外运行期开关选项，如 mode: rapid")
    project_id: Optional[str] = Field(default=None, description="操作挂载的项目ID，为空则尝试走全局默认")
    session_id: Optional[str] = Field(default=None, description="会话ID，用于隔离WebSocket及持久化会话记录")
