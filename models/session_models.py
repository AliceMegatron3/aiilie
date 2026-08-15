from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime, timezone
class SessionContext(BaseModel):
    session_id: str
    bind_project_id: Optional[str] = None
    bind_doc_id: Optional[str] = None
    bind_branch_id: Optional[str] = None
    model_key: str
    messages: List[dict] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_access_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_seconds: int = 43200  # 12 hours
    max_message_count: int = 25
    frozen: bool = False
    meta: dict = Field(default_factory=dict)
class SessionPoolStats(BaseModel):
    active_sessions: int
    frozen_sessions: int
    total_tokens_estimated: int
    disk_cache_mb: float