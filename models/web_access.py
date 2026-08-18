"""Data contracts for host-controlled public web fetching."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PublicFetchRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    agent_id: str = Field(default="host-agent", min_length=1, max_length=128)
    task_id: str = Field(default="", max_length=128)


class PublicFetchResult(BaseModel):
    url: str
    canonical_url: str
    title: str = ""
    text: str
    content_digest: str
    content_type: str
    truncated: bool = False


class WebAccessAudit(BaseModel):
    capability: Literal["web.fetch.public"] = "web.fetch.public"
    agent_id: str
    task_id: str = ""
    host: str
    resolved_ips: list[str] = Field(default_factory=list)
    content_digest: str = ""
    byte_count: int = 0
    outcome: Literal["ALLOWED", "DENIED", "FAILED"]
    reason: str = ""
    created_at: str = Field(default_factory=_now)


def content_digest(content: bytes) -> str:
    return sha256(content).hexdigest()
