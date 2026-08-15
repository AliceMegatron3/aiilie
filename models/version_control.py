from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
import uuid

# 默认分支 ID（平行宇宙根时间线）
DEFAULT_BRANCH_ID = "main"


class Branch(BaseModel):
    """补丁3 平行宇宙分支模型（架构整改 1.1 扩展为 DB 持久化实体）。"""
    branch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    parent_branch_id: Optional[str] = None
    is_active: bool = False
    # 补丁3：分支归档标记（归档分支只读保留，不参与活跃时间线切换）
    is_archived: bool = False
    # 架构整改 1.1 新增（Optional，向后兼容内存场景）：
    project_id: Optional[str] = None
    doc_id: Optional[str] = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class DocumentVersion(BaseModel):
    """文档在某分支上的内容快照。"""
    version_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    branch_id: str
    doc_id: str
    content: str
    commit_message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # 架构整改 1.1 新增（Optional，向后兼容内存场景）：
    project_id: Optional[str] = None
    # 版本序号（迁移自旧简易版本历史时保留，便于旧 API 语义兼容）
    version_no: Optional[int] = None


class VersionedProjectDocExtension(BaseModel):
    # 挂载到 Batch 3 ProjectDoc 的扩展字段
    current_branch_id: str = DEFAULT_BRANCH_ID
    branches: List[Branch] = Field(default_factory=lambda: [Branch(name="main", is_active=True)])
